#!/usr/bin/env python3
"""Scheduler throughput / memory benchmark.

Two program families:

1. Cheap-kernel (original): a top-level ``for i in range(0, E)`` (runtime-lazy,
   expanded by the engine) whose body static-expands into ~2*B nodes of cheap
   GIL-releasing kernels (test.spin). Values never grow, so this measures pure
   scheduler dispatch throughput (nodes/s, busy cores), not memory.

2. Large-payload (``--payload-mb > 0``): each inner kernel call is
   ``test.blob(seed, mb, rounds)``, a pure primitive that allocates an
   ``mb``-megabyte float64 numpy array and does GIL-releasing elementwise work
   on it, so the live tier and persist backlog carry realistic bytes. Two
   shapes select what happens to those arrays before they reach a loop's final
   sequence-assembly point:

   - default (bounded): each element's inner ``width`` blobs are immediately
     reduced with ``test.shrink`` (array -> scalar mean) before the outer
     fold/sequence combines them. Peak RSS here is bounded by however many
     blobs are concurrently *computing* (an admission-window question).
   - ``--sequence-floor``: the outer loop's body IS the blob (no shrink), and
     the goal is the raw spliced sequence (no fold). This exercises the
     structural fact that a loop's sequence node cannot compute until every
     body's value is simultaneously resident (see docs) — a floor no
     admission policy can remove, only bound by writing bodies to disk and
     evicting them before the sequence assembles.

RSS is sampled throughout the run (not just at the end) via
``engine.memlog.current_rss_bytes``, dep-free, so callers can plot/inspect a
full memory-over-time trace, not one point.

Usage:
  python tests/perf/bench_scheduler.py --elements 150 --width 1000 --rounds 4
  python tests/perf/bench_scheduler.py --payload-mb 8 --elements 64 --width 8
  python tests/perf/bench_scheduler.py --payload-mb 8 --elements 64 --sequence-floor
  python tests/perf/bench_scheduler.py --profile          # cProfile the event loop
  python tests/perf/bench_scheduler.py --no-cache         # without the disk tier
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "implementation" / "python"))


def build_program(elements: int, width: int, rounds: int, *, payload_mb: float,
                   sequence_floor: bool) -> str:
    """Select a program shape. payload_mb == 0 keeps the original cheap-kernel bench."""
    if payload_mb <= 0:
        literals = ",".join(str(j) for j in range(width))
        return f"""import "test"
let work(i,j) = spin(i * 1000000 + j, {rounds})
let body(i) = fold + (for j in [{literals}] do work(i,j))
print "total" fold + (for i in range(0, {elements}) do body(i))
"""
    if sequence_floor:
        # No shrink, no fold: the goal is the raw sequence, forcing every
        # element's array to stay resident until the whole loop has unrolled.
        return f"""import "test"
let bigwork(i) = blob(i, {payload_mb}, {rounds})
print "total" (for i in range(0, {elements}) do bigwork(i))
"""
    # Bounded: each inner blob is shrunk to a scalar before its consumers see
    # it, so only concurrently-*computing* blobs are ever large in the live tier.
    literals = ",".join(str(j) for j in range(width))
    return f"""import "test"
let work(i,j) = shrink(blob(i * 1000000 + j, {payload_mb}, {rounds}))
let body(i) = fold + (for j in [{literals}] do work(i,j))
print "total" fold + (for i in range(0, {elements}) do body(i))
"""


class _DelayedBackend:
    """Wraps a storage backend, sleeping in every write to simulate a slow disk."""

    def __init__(self, inner, delay_s: float):
        self._inner = inner
        self._delay_s = delay_s

    def put_success(self, *args, **kwargs):
        time.sleep(self._delay_s)
        return self._inner.put_success(*args, **kwargs)

    def put_success_batch(self, entries):
        time.sleep(self._delay_s * max(1, len(entries)))
        return self._inner.put_success_batch(entries)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class Sampler(threading.Thread):
    """Periodic sample: completed-node count, process CPU seconds, and RSS."""

    def __init__(self, engine, interval_s: float = 0.5):
        super().__init__(daemon=True)
        self.engine = engine
        self.interval_s = interval_s
        self.samples: list[tuple[float, int, float, int]] = []  # (wall, completed, cpu_s, rss_bytes)
        self._stop = threading.Event()

    def run(self):
        from voxlogica.engine.memlog import current_rss_bytes
        while not self._stop.is_set():
            t = time.monotonic()
            times = os.times()
            rss = current_rss_bytes()
            self.samples.append((t, len(self.engine.table.completed), times.user + times.system, rss))
            self._stop.wait(self.interval_s)

    def stop(self):
        self._stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--elements", type=int, default=150, help="top-level loop width E")
    parser.add_argument("--width", type=int, default=1000, help="inner static loop width B (~2B nodes/element)")
    parser.add_argument("--rounds", type=int, default=4, help="kernel work rounds")
    parser.add_argument("--threads", type=int, default=0, help="worker count (0 = cpu count)")
    parser.add_argument("--no-cache", action="store_true", help="run without the disk tier")
    parser.add_argument("--db", type=str, default="", help="reuse a specific results.db (warm-run tests)")
    parser.add_argument("--payload-mb", type=float, default=0.0,
                        help="MB per test.blob array (0 = original cheap-scalar bench)")
    parser.add_argument("--sequence-floor", action="store_true",
                        help="goal is the raw per-element blob sequence (no shrink/fold); "
                             "exercises the sequence-assembly memory floor")
    parser.add_argument("--slow-disk-ms", type=float, default=0.0,
                        help="sleep this many ms per persist write, to simulate a slow disk")
    parser.add_argument("--profile", action="store_true", help="cProfile the event-loop thread")
    parser.add_argument("--profile-out", type=str, default="", help="dump .pstats here")
    parser.add_argument("--progress", action="store_true", help="show the tqdm bar")
    parser.add_argument("--sample-interval", type=float, default=0.5, help="RSS sample interval, seconds")
    parser.add_argument("--trace-out", type=str, default="", help="write full (t,completed,rss_mb) trace here")
    parser.add_argument("--live-refresh", type=int, default=0,
                        help="(baseline-only ablation) override live_refresh_interval; "
                             "no-op on the frontier scheduler, which has no periodic walk")
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary")
    args = parser.parse_args()

    from voxlogica.engine.core import ComputationEngine
    from voxlogica.parser import parse_program_content
    from voxlogica.reducer import reduce_program
    from voxlogica.storage import SQLiteResultsDatabase

    t0 = time.monotonic()
    program = build_program(args.elements, args.width, args.rounds,
                             payload_mb=args.payload_mb, sequence_floor=args.sequence_floor)
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()
    t_plan = time.monotonic() - t0

    backend = None
    tmp = None
    if not args.no_cache:
        if args.db:
            backend = SQLiteResultsDatabase(db_path=args.db)
        else:
            tmp = tempfile.TemporaryDirectory(prefix="voxbench-")
            backend = SQLiteResultsDatabase(db_path=str(Path(tmp.name) / "results.db"))
        if args.slow_disk_ms > 0:
            backend = _DelayedBackend(backend, args.slow_disk_ms / 1000.0)

    engine = ComputationEngine(backend=backend, max_concurrency=args.threads,
                               progress=args.progress)
    if args.live_refresh and hasattr(engine.config, "live_refresh_interval"):
        import dataclasses  # only meaningful on the pre-frontier engine (ablation)
        engine.config = dataclasses.replace(engine.config, live_refresh_interval=args.live_refresh)
    engine.adopt_plan(plan)
    for goal in plan.goals:
        engine.submit(goal.id, goal.operation, goal.name)

    sampler = Sampler(engine, interval_s=args.sample_interval)
    times0 = os.times()
    cpu0 = times0.user + times0.system
    t1 = time.monotonic()
    sampler.start()

    import asyncio
    if args.profile:
        import cProfile
        import pstats
        prof = cProfile.Profile()
        prof.runcall(asyncio.run, engine.run())
    else:
        asyncio.run(engine.run())

    wall = time.monotonic() - t1
    sampler.stop()
    times1 = os.times()
    cpu = times1.user + times1.system - cpu0

    completed = len(engine.table.completed)
    samples = sampler.samples
    first = next((s[0] - t1 for s in samples if s[1] > 0), wall)
    peak_rss = max((s[3] for s in samples), default=0)

    # Per-5-sample throughput/busy-core timeline (~2.5s at the default interval).
    timeline = []
    for i in range(5, len(samples), 5):
        (ta, ca, ua, _ra), (tb, cb, ub, rb) = samples[i - 5], samples[i]
        timeline.append((round(tb - t1, 1), round((cb - ca) / max(1e-9, tb - ta), 1),
                         round((ub - ua) / max(1e-9, tb - ta), 1), round(rb / 1024 / 1024, 1)))

    m = engine.metrics()
    summary = {
        "elements": args.elements, "width": args.width, "rounds": args.rounds,
        "payload_mb": args.payload_mb, "sequence_floor": args.sequence_floor,
        "threads": engine.max_concurrency, "cache": not args.no_cache,
        "slow_disk_ms": args.slow_disk_ms,
        "plan_nodes": len(plan.nodes), "plan_build_s": round(t_plan, 2),
        "total_nodes": len(engine.table.nodes), "completed": completed,
        "wall_s": round(wall, 2), "nodes_per_s": round(completed / wall, 1),
        "first_completion_s": round(first, 2),
        "mean_busy_cores": round(cpu / wall, 1),
        "peak_rss_mb": round(peak_rss / 1024 / 1024, 1),
        "metrics": m,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print("\n== bench_scheduler summary ==")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        print("  timeline (t_s, nodes/s, busy_cores, rss_mb):")
        for row in timeline:
            print(f"    {row}")

    if args.trace_out:
        with open(args.trace_out, "w") as fh:
            fh.write("t_s\tcompleted\trss_mb\n")
            for s in samples:
                fh.write(f"{s[0] - t1:.2f}\t{s[1]}\t{s[3] / 1024 / 1024:.2f}\n")

    if args.profile:
        stats = pstats.Stats(prof)
        stats.sort_stats("cumulative")
        print("\n== event-loop profile (cumulative, top 40) ==")
        stats.print_stats(40)
        stats.sort_stats("tottime")
        print("\n== event-loop profile (tottime, top 40) ==")
        stats.print_stats(40)
        if args.profile_out:
            stats.dump_stats(args.profile_out)

    if backend is not None:
        backend.close()
    if tmp is not None:
        tmp.cleanup()


if __name__ == "__main__":
    main()
