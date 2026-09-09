#!/usr/bin/env python3
"""Read measurement files and print a table that states its own method.

THE RULE THIS SCRIPT EXISTS TO ENFORCE: a rate is computed from consecutive
sample timestamps, never from the requested period. Dividing by the nominal
period overstated CPU by 39% in the measurement this framework replaced, and
the mistake is invisible in the output unless something refuses to make it.

The headline figure is never a sampled mean. It comes from the file's
`authoritative` block, which the kernel filled in via getrusage at start and
exit and which therefore has no sampling error. The sampled series is used only
for the shape: how much of the run was saturated, and where it was not.

THE FOUR QUESTIONS ONE RUN NOW ANSWERS. A 24-core sweep sitting at 1675-1934%
of 2400% has four candidate explanations, and a table that reports only CPU can
distinguish none of them:

  * the device is the ceiling            -> `dev util` at ~100%
  * we are the ones waiting for it       -> `thr D` well above zero
  * the event loop is the ceiling        -> `loop>=90%` high while `mean CPU` is low
  * none of the above                    -> device idle, no D threads, loop cool

`dev util` is io_ticks per unit of measured wall time -- the fraction of the
interval during which the device had at least one request in flight -- and is
the only one of these that can be called saturation. Throughput cannot: a
device is pinned at 100% by a trickle of small synchronous writes at 3 MB/s and
also by a streaming write at 2 GB/s, and only the first is a bottleneck.

`dev MB/s w` is what reached the device; `proc MB/s w` is this process's
write_bytes. They differ, legitimately, by other tenants of the device and by
page-cache writeback timing, so a mode that claims to write nothing is checked
against `proc MB/s w`, and saturation against `dev util`.

Usage:
    report.py <measurement.tsv> [more.tsv ...]
    report.py --series <measurement.tsv>      # per-interval rates, for plotting
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> tuple[dict, list[str], list[list[str]]]:
    header_lines, columns, rows = [], [], []
    with open(path, encoding="utf-8") as handle:
        first = handle.readline()
        if not first.startswith("# voxlogica measurement"):
            raise ValueError("not a measurement file (no version line)")
        for line in handle:
            line = line.rstrip("\n")
            if line.startswith("# voxlogica measurement"):
                continue
            if line.startswith("# "):
                header_lines.append(line[2:])
            elif not columns:
                columns = line.split("\t")
            else:
                rows.append(line.split("\t"))
    return json.loads("\n".join(header_lines)), columns, rows


def _cell(row: list[str], index: int | None) -> int | None:
    """One integer, or None for a column that is absent or an empty cell.

    Empty is not zero. The thread census fires on one tick in four and leaves
    its cells blank on the others; reading those as zero would report "no
    threads in disk wait" for three quarters of every run.
    """
    if index is None or index >= len(row):
        return None
    text = row[index].strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return None if value < 0 else value


def derive(header: dict, columns: list[str], rows: list[list[str]]) -> list[dict]:
    """One dict per INTERVAL, every rate divided by the elapsed t_ns delta.

    Per interval and not per sample, because a rate needs two readings, and the
    interval used is the one that actually elapsed -- the whole point of the
    framework. Fields whose source columns are missing or blank come back None
    rather than 0, so a reader cannot average an absence into a number.
    """
    ticks = header.get("ticks_per_second", 100)
    sector = header.get("sector_bytes", 512)
    index = {name: columns.index(name) for name in columns}
    t = index["t_ns"]

    def at(name: str) -> int | None:
        return index.get(name)

    out: list[dict] = []
    t0 = int(rows[0][t]) if rows else 0
    for before, after in zip(rows, rows[1:]):
        dt = (int(after[t]) - int(before[t])) / 1e9
        if dt <= 0:
            continue

        def rate(name: str, scale: float) -> float | None:
            lo, hi = _cell(before, at(name)), _cell(after, at(name))
            if lo is None or hi is None:
                return None
            return (hi - lo) * scale / dt

        point = {
            "t_s": (int(after[t]) - t0) / 1e9,
            "dt_s": dt,
            "proc_cpu_pct": rate("proc_ticks", 100.0 / ticks),
            "loop_cpu_pct": rate("loop_ticks", 100.0 / ticks),
            # io_ticks are milliseconds of device-busy per second of wall time:
            # 1000 ms per 1 s is 100% utilised, hence the /10 to reach percent.
            "dev_util_pct": rate("dev_io_ticks_ms", 0.1),
            # Weighted io ms per unit time is dimensionless: the mean number of
            # requests in flight, i.e. queue depth. >1 means requests queued.
            "dev_queue": rate("dev_weighted_io_ms", 1e-3),
            "dev_write_mb_s": rate("dev_sectors_written", sector / 1e6),
            "dev_read_mb_s": rate("dev_sectors_read", sector / 1e6),
            "proc_write_mb_s": rate("io_write_bytes", 1e-6),
            "proc_read_mb_s": rate("io_read_bytes", 1e-6),
            "proc_wchar_mb_s": rate("io_wchar", 1e-6),
            # Per-sample states, not rates: reported at the interval's end.
            "thr_total": _cell(after, at("thr_total")),
            "thr_running": _cell(after, at("thr_running")),
            "thr_disk": _cell(after, at("thr_disk")),
            # The engine's own view of how much work it HAD. Idle cores with
            # `ready + in_flight` below the core count are not a resource
            # problem at all, and no other column can tell that case apart.
            "ready": _cell(after, at("ready")),
            "in_flight": _cell(after, at("in_flight")),
        }
        out.append(point)
    return out


def series(header: dict, columns: list[str], rows: list[list[str]]):
    """(seconds_from_start, process CPU%, loop-thread CPU%) per interval.

    The original three-column view, kept because plotting scripts and the test
    that pins the 39% rule both call it.
    """
    return [(p["t_s"], p["proc_cpu_pct"], p["loop_cpu_pct"])
            for p in derive(header, columns, rows)]


def weighted_mean(points: list[dict], key: str) -> float | None:
    """Duration-weighted mean of a per-interval rate, over intervals that have it.

    Weighted by dt because the sampler's achieved period jitters (up to 2.5 s
    has been observed under load) and an unweighted mean would let the short,
    quiet intervals outvote the long, busy ones.
    """
    num = den = 0.0
    for point in points:
        value = point.get(key)
        if value is None:
            continue
        num += value * point["dt_s"]
        den += point["dt_s"]
    return num / den if den > 0 else None


def census_mean(points: list[dict], key: str) -> tuple[float | None, int]:
    """Mean over the samples that CARRY a census, and how many those were.

    The count is returned with the mean because a mean of two samples and a
    mean of two hundred are not the same claim.
    """
    values = [p[key] for p in points if p.get(key) is not None]
    if not values:
        return None, 0
    return sum(values) / len(values), len(values)


def _fmt(value: float | None, spec: str, suffix: str = "") -> str:
    return "-" if value is None else f"{value:{spec}}{suffix}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--series":
        header, columns, rows = load(Path(argv[1]))
        keys = ["proc_cpu_pct", "loop_cpu_pct", "dev_util_pct", "dev_queue",
                "dev_write_mb_s", "proc_write_mb_s", "thr_total", "thr_running",
                "thr_disk"]
        print("\t".join(["t_s", *keys]))
        for point in derive(header, columns, rows):
            cells = [f"{point['t_s']:.3f}"]
            for key in keys:
                value = point[key]
                cells.append("" if value is None else f"{value:.2f}")
            print("\t".join(cells))
        return 0

    print("target: where the ceiling is in one voxlogica run -- CPU, the event")
    print("        loop, or the block device under the store")
    print("method: getrusage(RUSAGE_SELF) at start and exit for the totals;")
    print("        in-process sampling on its own thread for the shape, rates")
    print("        computed from measured intervals (engine/measure.py).")
    print("        Device figures are /proc/diskstats io_ticks and sectors for")
    print("        the device backing the store; proc figures are")
    print("        /proc/self/io; thr D is our own threads in uninterruptible")
    print("        sleep, sampled once a second.")
    print()
    head = ("file", "outcome", "wall s", "mean CPU", "of", "peak RSS",
            "sat>=90%", "loop>=90%", "dev util", "dev q", "dev MB/s w",
            "proc MB/s w", "thr D", "thr R", "of thr", "device", "instrument",
            "commit")
    print("\t".join(head))
    for name in argv:
        path = Path(name)
        try:
            header, columns, rows = load(path)
        except Exception as exc:                            # noqa: BLE001
            if "not a measurement file" not in str(exc):
                print(f"{path.name}\tunreadable: {exc}")
            continue
        auth = header.get("authoritative", {})
        inst = header.get("instrument", {})
        cores = auth.get("cores_available") or 1
        pts = derive(header, columns, rows) if len(rows) > 1 else []
        ceiling = 100.0 * cores
        sat = sum(1 for p in pts
                  if p["proc_cpu_pct"] is not None and p["proc_cpu_pct"] >= 0.90 * ceiling)
        loop_hot = sum(1 for p in pts
                       if p["loop_cpu_pct"] is not None and p["loop_cpu_pct"] >= 90.0)
        share = inst.get("sampler_share_of_run_cpu")
        out = header.get("outcome", {})
        if not out.get("recorded"):
            verdict = "?"
        elif out.get("complete"):
            verdict = "complete"
        else:
            verdict = f"INCOMPLETE {out.get('goals_resolved')}/{out.get('goals_total')}"
        d_mean, d_n = census_mean(pts, "thr_disk")
        r_mean, _ = census_mean(pts, "thr_running")
        t_mean, _ = census_mean(pts, "thr_total")
        print("\t".join([
            path.name,
            verdict,
            f"{auth.get('wall_s', 0):.1f}",
            f"{auth.get('mean_cpu_percent')}%",
            f"{ceiling:.0f}%",
            f"{(auth.get('peak_rss_bytes') or 0) / 1e9:.1f} GB",
            f"{sat}/{len(pts)}" if pts else "-",
            f"{loop_hot}/{len(pts)}" if pts else "-",
            _fmt(weighted_mean(pts, "dev_util_pct"), ".0f", "%"),
            _fmt(weighted_mean(pts, "dev_queue"), ".2f"),
            _fmt(weighted_mean(pts, "dev_write_mb_s"), ".1f"),
            _fmt(weighted_mean(pts, "proc_write_mb_s"), ".1f"),
            f"{_fmt(d_mean, '.2f')}/{d_n}",
            _fmt(r_mean, ".1f"),
            _fmt(t_mean, ".0f"),
            str(header.get("block_device") or "-"),
            (f"{share:.4%}" if isinstance(share, float) else str(share)),
            (header.get("commit") or "")[:8] + ("+dirty" if header.get("working_tree_dirty") else ""),
        ]))
    print()
    print("outcome:   a run that did not resolve every goal is INCOMPLETE, and")
    print("           its timings mean nothing: a sweep that aborts early looks")
    print("           fast. Compare only complete runs.")
    print("sat>=90%:  samples at or above 90% of every core busy")
    print("loop>=90%: samples where the event-loop thread alone held ~a full")
    print("           core -- with a low process figure beside it, that is the")
    print("           starvation signature: workers idle, waiting on the loop")
    print("dev util:  fraction of wall time the device had a request in flight")
    print("           (io_ticks/elapsed). 100% is saturation; MB/s is not.")
    print("dev q:     mean requests in flight (weighted io ms / elapsed). Above")
    print("           1 means requests were queueing behind each other.")
    print("thr D:     mean of OUR threads in uninterruptible sleep, over the")
    print("           N samples that carry a census (shown as mean/N). A busy")
    print("           device with thr D at 0 is somebody else's I/O, not ours.")
    print("-:         not measured on this host, which is not the same as zero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
