#!/usr/bin/env python3
"""Split the shortfall from full CPU into the causes the sample rows can see.

THE QUESTION. A 24-core run that averages 1900% of 2400% has left 500% of a
core-second per second on the floor, every second, and "mean CPU" cannot say
where it went. This script converts the same rows report.py tabulates into
CORE-SECONDS OF SHORTFALL and asks, interval by interval, which of the four
candidate explanations was present at the time:

  work-starved  the engine had fewer runnable nodes than the machine has cores
                (`ready` + `in_flight` from the engine's own snapshot). Nothing
                to do with any resource: the program's dependency graph had
                narrowed. This is the only class that no amount of I/O or
                scheduling work can recover.
  loop-bound    the asyncio thread alone held >=90% of a core. The engine's
                dispatch, completion and eviction bookkeeping is serial, so a
                saturated loop caps the whole process however many workers wait.
  disk-wait     at least one of our threads was in D state, i.e. uninterruptible
                sleep in the kernel, which in practice means waiting on I/O.
                Only intervals carrying a thread census can answer this, so the
                census coverage is reported next to the number.
  unexplained   none of the above was present and the cores were still idle.

WHAT THIS IS AND IS NOT. Coincidence is not causation, and the classes overlap:
an interval can be work-starved AND loop-bound. So two views are printed. The
"coincident" view charges the whole interval's shortfall to every class present
in it -- each figure is therefore an UPPER BOUND on that cause, and they sum to
more than 100%. The "partition" view charges each interval to exactly one class
under a stated precedence, so the four figures sum to the shortfall exactly.
Precedence puts work-starvation first because a machine with nothing to run is
not waiting for a resource, and neither the disk nor the loop can be blamed for
idleness that no resource would have filled.

There is also one bound that needs no classification at all: a thread in D state
is a thread not on a CPU, so `mean(threads in D) x the measured span` is
directly the core-seconds those threads would have contributed had the device
been infinitely fast. That is printed as `D-thread bound`, and it is the honest
ceiling on the disk hypothesis -- the whole budget it has to spend. It rests on
one assumption and no classification: that the censused quarter of the run
represents the intervals between the censuses. The unextended, purely measured
figure is kept in the result under `d_thread_bound_census_core_s`.

AND ONE TABLE THAT BEATS ALL OF THE ABOVE. Any single threshold on the loop
thread's CPU is arbitrary, and the choice moves the answer: a loop at 85% is
not obviously innocent. So the second block prints the whole DOSE-RESPONSE --
mean process CPU and shortfall, bucketed by how hot the loop was in that
interval. A monotone fall in process CPU as the loop's own CPU rises is an
argument no threshold has to be defended for.

Every rate still comes from consecutive t_ns (see engine/measure.py).

Usage: attribution.py <measurement.tsv> [more.tsv ...]
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "vox_measure_report_for_attribution", Path(__file__).resolve().parent / "report.py")
report = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(report)

#: A hot event loop is one holding ~a whole core. 90% rather than 100% because
#: the sampler's own tick lands inside the interval and the loop thread yields
#: on every await; a loop measured at 100.0% has never been observed.
LOOP_HOT_PCT = 90.0

#: Buckets for the dose-response table, in percent of one core held by the
#: event-loop thread. Edges chosen before looking at the data and left alone.
LOOP_BANDS = ((0.0, 25.0), (25.0, 50.0), (50.0, 75.0), (75.0, 90.0), (90.0, 1e9))


def classify(header: dict, columns: list[str], rows: list[list[str]]) -> dict:
    cores = header.get("authoritative", {}).get("cores_available") or 1
    ceiling = 100.0 * cores
    points = report.derive(header, columns, rows)

    total_dt = 0.0
    shortfall = 0.0                  # core-seconds the machine could have run
    coincident = {"work-starved": 0.0, "loop-bound": 0.0, "disk-wait": 0.0}
    partition = {"work-starved": 0.0, "loop-bound": 0.0, "disk-wait": 0.0,
                 "unexplained": 0.0}
    census_dt = census_shortfall = 0.0
    d_weighted = 0.0

    for point in points:
        cpu = point["proc_cpu_pct"]
        if cpu is None:
            continue
        dt = point["dt_s"]
        total_dt += dt
        idle = max(0.0, ceiling - cpu) / 100.0 * dt
        shortfall += idle

        runnable = None
        ready, in_flight = point.get("ready"), point.get("in_flight")
        if ready is not None and in_flight is not None:
            runnable = ready + in_flight
        starved = runnable is not None and runnable < cores
        loop = point["loop_cpu_pct"]
        loop_bound = loop is not None and loop >= LOOP_HOT_PCT
        d_threads = point["thr_disk"]
        if d_threads is not None:
            census_dt += dt
            census_shortfall += idle
            d_weighted += d_threads * dt
        disk_wait = d_threads is not None and d_threads >= 1

        if starved:
            coincident["work-starved"] += idle
        if loop_bound:
            coincident["loop-bound"] += idle
        if disk_wait:
            coincident["disk-wait"] += idle

        # Precedence: idleness with nothing to run is nobody's fault, so it is
        # charged first and neither the disk nor the loop inherits it.
        if starved:
            partition["work-starved"] += idle
        elif loop_bound:
            partition["loop-bound"] += idle
        elif disk_wait:
            partition["disk-wait"] += idle
        else:
            partition["unexplained"] += idle

    bands = []
    for low_edge, high_edge in LOOP_BANDS:
        sel = [p for p in points
               if p["proc_cpu_pct"] is not None and p["loop_cpu_pct"] is not None
               and low_edge <= p["loop_cpu_pct"] < high_edge]
        if not sel:
            bands.append((low_edge, high_edge, 0, None, 0.0, 0.0))
            continue
        span = sum(p["dt_s"] for p in sel)
        cpu = sum(p["proc_cpu_pct"] * p["dt_s"] for p in sel) / span
        idle = sum(max(0.0, ceiling - p["proc_cpu_pct"]) / 100.0 * p["dt_s"] for p in sel)
        bands.append((low_edge, high_edge, len(sel), cpu, idle, span))

    return {
        "cores": cores,
        "bands": bands,
        "intervals": len(points),
        "measured_s": total_dt,
        "shortfall_core_s": shortfall,
        "coincident": coincident,
        "partition": partition,
        "census_coverage": (census_dt / total_dt) if total_dt > 0 else 0.0,
        "census_shortfall_core_s": census_shortfall,
        # Threads in D would have been on a CPU had the device been infinitely
        # fast: this is the disk hypothesis' whole budget, in core-seconds.
        # The measured figure covers only the intervals that carry a census
        # (a quarter of the run at the default cadence); the full-run figure
        # extends the mean across the whole measured span, and is the one to
        # compare against the shortfall. Both are kept, because the first is a
        # measurement and the second is a measurement times an assumption --
        # that the sampled quarter is representative of the rest.
        "d_thread_bound_census_core_s": d_weighted if census_dt > 0 else None,
        "d_thread_bound_core_s": ((d_weighted / census_dt) * total_dt
                                  if census_dt > 0 else None),
        "mean_d_threads": (d_weighted / census_dt) if census_dt > 0 else None,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    print("target: where the CPU that a run did NOT use went, in core-seconds")
    print("method: per-interval shortfall from cores x 100%, classified by the")
    print("        engine's own runnable count, the loop thread's CPU and the")
    print("        count of our threads in D state. Rates from measured")
    print("        intervals only (engine/measure.py).")
    print()
    head = ("file", "cores", "shortfall core-s", "starved", "loop", "disk",
            "unexplained", "D-thread bound", "mean D", "census cover")
    print("\t".join(head))
    for name in argv:
        path = Path(name)
        try:
            header, columns, rows = report.load(path)
        except Exception as exc:                            # noqa: BLE001
            if "not a measurement file" not in str(exc):
                print(f"{path.name}\tunreadable: {exc}")
            continue
        if len(rows) < 2:
            print(f"{path.name}\ttoo few samples for a rate")
            continue
        out = header.get("outcome", {})
        verdict = "" if out.get("complete") else " [INCOMPLETE]"
        result = classify(header, columns, rows)
        total = result["shortfall_core_s"] or 1.0

        def pct(value: float | None) -> str:
            return "-" if value is None else f"{100.0 * value / total:.0f}%"

        print("\t".join([
            path.name + verdict,
            str(result["cores"]),
            f"{result['shortfall_core_s']:.1f}",
            pct(result["partition"]["work-starved"]),
            pct(result["partition"]["loop-bound"]),
            pct(result["partition"]["disk-wait"]),
            pct(result["partition"]["unexplained"]),
            pct(result["d_thread_bound_core_s"]),
            ("-" if result["mean_d_threads"] is None
             else f"{result['mean_d_threads']:.2f}"),
            f"{100.0 * result['census_coverage']:.0f}%",
        ]))
    print()
    print("dose-response: how much CPU the process got, by how hot the loop was")
    print("file\tloop band\tintervals\ts\tmean proc CPU\tshortfall core-s\tshare")
    for name in argv:
        path = Path(name)
        try:
            header, columns, rows = report.load(path)
        except Exception:                                   # noqa: BLE001
            continue
        if len(rows) < 2:
            continue
        result = classify(header, columns, rows)
        total = result["shortfall_core_s"] or 1.0
        for low_edge, high_edge, count, cpu, idle, span in result["bands"]:
            label = (f"{low_edge:.0f}-{high_edge:.0f}%" if high_edge < 1e8
                     else f">={low_edge:.0f}%")
            print("\t".join([path.name, label, str(count), f"{span:.1f}",
                             "-" if cpu is None else f"{cpu:.0f}%",
                             f"{idle:.1f}", f"{100.0 * idle / total:.0f}%"]))
    print()
    print("starved/loop/disk/unexplained partition each interval's shortfall to")
    print("  ONE class, precedence work-starved > loop-bound > disk-wait, so the")
    print("  four columns sum to 100% of the shortfall.")
    print("D-thread bound: mean threads in D x the whole measured span, as a")
    print("  share of the shortfall. A thread in D is a thread not on a CPU, so")
    print("  this is the WHOLE budget the disk hypothesis has, and it needs no")
    print("  classification to be true -- only that the sampled censuses are")
    print("  representative of the intervals between them.")
    print("census cover: share of measured time carrying a thread census; the")
    print("  disk column can only see that much of the run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
