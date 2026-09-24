#!/usr/bin/env python3
"""Run the performance suite and write one metrics file.

WHAT THIS IS FOR. Section 6 of the TACAS paper states a small number of claims
about the engine, and each of them has to come from a measurement that somebody
else can repeat. This script runs those measurements and writes their results
to a single JSON file; the table and the figures in the paper are generated
from that file by scripts in the paper repository, so the measurement lives
beside the thing measured and the presentation lives beside the prose.

WHAT IT MEASURES, AND WHY EACH ONE IS SHAPED THIS WAY.

  scaling   The same program at several thread counts. Wall-clock alone is not
            enough: a change that buys wall time with extra processor time is
            not an improvement, so the CPU cost of one completion is recorded
            next to it and is expected to stay flat. The saturated fraction
            says how much of the time the process was near the machine's
            ceiling, which is what distinguishes a scheduler that stops feeding
            the workers from kernels that have stopped getting faster.

  cache     The same program twice against the same store. The second run is
            the claim in Section 4 that a repeated program returns at once.

  fusion    The same program with element-wise fusion enabled and disabled.
            Without the comparison, fusion is a design decision rather than a
            result.

  resume    A run stopped after a fixed number of completions, and then
            restarted against the store it left. The interesting quantity is
            how much work the second run does: if the store is used as
            intended, the two runs together do about as much as one run.

TIME BUDGET. The whole suite is meant to finish in about ten minutes on an
idle machine, so that it can be repeated whenever the engine changes rather
than once before a deadline. The benchmark program reads no dataset, which
keeps it runnable anywhere; pass --program to point the same measurements at
the case-study program instead, which is how the figures in the paper are
produced.

USAGE
    python tools/perf/run_suite.py --out metrics.json
    python tools/perf/run_suite.py --program path/to/case_study.imgql --out m.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_PROGRAM = HERE / "bench.imgql"

#: Completions after which the interrupted run of the `resume` measurement
#: stops. Half of what the program does is the informative point: a run that
#: ignores the store does the whole program again, a run that uses it does the
#: remainder, and the two outcomes are a factor of two apart.
RESUME_STOP = 40_000


def _cli(program: Path, store: Path, report: Path, threads: int,
         extra: list[str] | None = None) -> list[str]:
    """The command line for one run.

    `--no-serve` keeps the process from starting the web interface, `--measure`
    writes the report this script reads, and the store is always named
    explicitly so that no measurement can accidentally inherit another one's
    cache.
    """
    command = [sys.executable, "-u", "-m", "voxlogica.main", "run", "--no-serve",
               "--threads", str(threads),
               "--store-db", str(store),
               "--measure", str(report)]
    return command + list(extra or []) + [str(program)]


def _environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "implementation" / "python") + os.pathsep + env.get("PYTHONPATH", "")
    # Every measurement decides its own stopping rule; an inherited one would
    # silently truncate a run that is supposed to go to the end.
    env.pop("VOXLOGICA_DEV_STOP_AFTER", None)
    env.update(extra or {})
    return env


def _read_report(report: Path) -> dict:
    """The fields of the measurement report that Section 6 quotes.

    Read by name rather than by position in the document, so that a change in
    the report's shape shows up as a missing field instead of as a wrong
    number.
    """
    if not report.is_file():
        return {}
    payload = json.loads(report.read_text())
    throughput = payload.get("throughput") or {}
    shape = payload.get("shape") or {}
    totals = payload.get("totals") or {}
    work = payload.get("work") or {}
    loop = payload.get("event_loop") or {}
    return {
        "completions": throughput.get("completions"),
        "completions_per_second": throughput.get("completions_per_second"),
        "cpu_ms_per_completion": throughput.get("cpu_seconds_per_completion_ms"),
        "cpu_mean_percent": shape.get("process_cpu_mean_percent"),
        "cpu_max_percent": shape.get("process_cpu_max_percent"),
        "saturated_fraction_90": shape.get("saturated_fraction_90"),
        "loop_occupancy_percent": loop.get("mean_occupancy_percent"),
        "kernels": work.get("kernels_executed"),
        "recomputes": work.get("recomputes"),
        "cones": work.get("cones_dispatched"),
        "ops_fused": work.get("ops_fused"),
        "wall_seconds": totals.get("wall_seconds"),
        "cpu_seconds": totals.get("cpu_seconds"),
        # Resident memory is reported in bytes and quoted in megabytes, which
        # is the unit the section uses. `getrusage` is the source, so this is
        # what the process actually occupied and not the engine's own tally.
        "peak_rss_mb": (round(totals["peak_rss_bytes"] / 1024 ** 2, 1)
                        if totals.get("peak_rss_bytes") else None),
        "cores_available": totals.get("cores_available"),
    }


def _run(name: str, program: Path, store: Path, threads: int, work: Path,
         extra_args: list[str] | None = None,
         extra_env: dict[str, str] | None = None) -> dict:
    """One measured run, returning its record."""
    report = work / f"{name}.report.json"
    started = time.monotonic()
    completed = subprocess.run(
        _cli(program, store, report, threads, extra_args),
        env=_environment(extra_env), cwd=str(REPO),
        capture_output=True, text=True, timeout=3600)
    elapsed = time.monotonic() - started
    record = {
        "measurement": name,
        "threads": threads,
        "exit_code": completed.returncode,
        "elapsed_seconds": round(elapsed, 2),
    }
    record.update(_read_report(report))
    if completed.returncode != 0:
        # Kept, not raised: one measurement failing should not throw away the
        # others, and a record with a non-zero exit is visible in the table.
        record["stderr_tail"] = completed.stderr[-600:]
    return record


def measure_scaling(program: Path, work: Path, thread_counts: list[int]) -> list[dict]:
    """The same work at several widths, each against its own empty store.

    A fresh store per run, because a run that found the previous one's results
    would measure the cache instead of the scheduler.
    """
    records = []
    for threads in thread_counts:
        store = work / f"scaling-{threads}.db"
        records.append(_run(f"scaling-{threads}", program, store, threads, work))
        _discard(store)
    return records


def measure_cache(program: Path, work: Path, threads: int) -> list[dict]:
    """Cold then warm against one store."""
    store = work / "cache.db"
    _discard(store)
    cold = _run("cache-cold", program, store, threads, work)
    warm = _run("cache-warm", program, store, threads, work)
    _discard(store)
    return [cold, warm]


def measure_fusion(program: Path, work: Path, threads: int) -> list[dict]:
    """With and without element-wise fusion, each against an empty store."""
    records = []
    for label, env in (("fusion-on", {}), ("fusion-off", {"VOXLOGICA_FUSION": "0"})):
        store = work / f"{label}.db"
        _discard(store)
        records.append(_run(label, program, store, threads, work, extra_env=env))
        _discard(store)
    return records


def measure_resume(program: Path, work: Path, threads: int) -> list[dict]:
    """Stop a run partway, then start it again against what it left.

    The stop is the engine's own development guard, which stops between
    completions and then flushes and drains exactly as a finished run does. A
    run killed with a signal would leave a store missing its last transaction,
    which is a different measurement.
    """
    store = work / "resume.db"
    _discard(store)
    reference = _run("resume-reference", program, store, threads, work)
    _discard(store)
    stopped = _run("resume-stopped", program, store, threads, work,
                   extra_env={"VOXLOGICA_DEV_STOP_AFTER": str(RESUME_STOP)})
    resumed = _run("resume-resumed", program, store, threads, work)
    _discard(store)
    return [reference, stopped, resumed]


def _discard(store: Path) -> None:
    """Remove a store and everything that belongs to it."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(store) + suffix).unlink(missing_ok=True)
    payloads = Path(str(store) + ".files")
    if payloads.is_dir():
        shutil.rmtree(payloads, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM,
                        help="program to measure (default: the bundled benchmark)")
    parser.add_argument("--out", type=Path, default=Path("metrics.json"),
                        help="where to write the metrics")
    parser.add_argument("--threads", type=int, default=0,
                        help="width for the single-width measurements "
                             "(default: as many as the machine has)")
    parser.add_argument("--thread-counts", default="",
                        help="comma-separated widths for the scaling measurement")
    parser.add_argument("--only", default="",
                        help="comma-separated subset of: scaling,cache,fusion,resume")
    arguments = parser.parse_args(argv[1:])

    cores = os.cpu_count() or 8
    threads = arguments.threads or cores
    if arguments.thread_counts:
        counts = [int(value) for value in arguments.thread_counts.split(",")]
    else:
        # Powers of two up to the machine's width, with the width itself last,
        # which is where a scheduler that has stopped scaling shows it.
        counts = [c for c in (1, 2, 4, 8, 16, 32) if c <= cores]
        if cores not in counts:
            counts.append(cores)
    wanted = set(filter(None, arguments.only.split(","))) or {
        "scaling", "cache", "fusion", "resume"}

    work = Path(tempfile.mkdtemp(prefix="vlperf-"))
    records: list[dict] = []
    try:
        if "scaling" in wanted:
            records += measure_scaling(arguments.program, work, counts)
        if "cache" in wanted:
            records += measure_cache(arguments.program, work, threads)
        if "fusion" in wanted:
            records += measure_fusion(arguments.program, work, threads)
        if "resume" in wanted:
            records += measure_resume(arguments.program, work, threads)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    payload = {
        "program": str(arguments.program),
        "resume_stop_after": RESUME_STOP,
        "machine": {
            "cores": cores,
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": records,
    }
    arguments.out.write_text(json.dumps(payload, indent=2))
    print(f"{len(records)} measurements written to {arguments.out}")
    for record in records:
        print("  %-18s threads=%-3s wall=%8ss completions=%-9s cpu_ms/completion=%s" % (
            record["measurement"], record["threads"],
            record.get("elapsed_seconds"), record.get("completions"),
            record.get("cpu_ms_per_completion")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
