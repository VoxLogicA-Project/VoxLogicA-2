#!/usr/bin/env python3
"""Scheduler throughput benchmark: one wide runtime loop, ~2k-node bodies.

Reproduces the production pathology at adjustable scale: a top-level
``for i in range(0, E)`` (runtime-lazy, expanded by the engine) whose body
static-expands into ~2*B nodes of cheap GIL-releasing kernels (test.spin).
The kernels scale linearly across threads, so sustained nodes/s and core
utilization measure *scheduler* dispatch capacity, not kernel speed.

Usage:
  python tests/perf/bench_scheduler.py --elements 150 --width 1000 --rounds 4
  python tests/perf/bench_scheduler.py --profile          # cProfile the event loop
  python tests/perf/bench_scheduler.py --no-cache         # without the disk tier

Reports: plan-build time, time-to-first-completion (expansion stall), sustained
nodes/s (total and per-5s timeline), mean busy cores, engine metrics.
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


def build_program(elements: int, width: int, rounds: int) -> str:
    """One runtime-lazy top loop over `elements`; each body ~2*width spin nodes."""
    literals = ",".join(str(j) for j in range(width))
    return f"""import "test"
let work(i,j) = spin(i * 1000000 + j, {rounds})
let body(i) = fold + (for j in [{literals}] do work(i,j))
print "total" fold + (for i in range(0, {elements}) do body(i))
"""


class Sampler(threading.Thread):
    """Once per second: completed-node count and process CPU seconds (all threads)."""

    def __init__(self, engine):
        super().__init__(daemon=True)
        self.engine = engine
        self.samples: list[tuple[float, int, float]] = []  # (wall, completed, cpu_s)
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            t = time.monotonic()
            times = os.times()
            self.samples.append((t, len(self.engine.table.completed), times.user + times.system))
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elements", type=int, default=150, help="top-level loop width E")
    parser.add_argument("--width", type=int, default=1000, help="inner static loop width B (~2B nodes/element)")
    parser.add_argument("--rounds", type=int, default=4, help="spin rounds per kernel (~0.5ms each)")
    parser.add_argument("--threads", type=int, default=0, help="worker count (0 = cpu count)")
    parser.add_argument("--no-cache", action="store_true", help="run without the disk tier")
    parser.add_argument("--db", type=str, default="", help="reuse a specific results.db (warm-run tests)")
    parser.add_argument("--profile", action="store_true", help="cProfile the event-loop thread")
    parser.add_argument("--profile-out", type=str, default="", help="dump .pstats here")
    parser.add_argument("--progress", action="store_true", help="show the tqdm bar")
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
    plan = reduce_program(parse_program_content(
        build_program(args.elements, args.width, args.rounds))).to_symbolic_plan()
    t_plan = time.monotonic() - t0

    backend = None
    tmp = None
    if not args.no_cache:
        if args.db:
            backend = SQLiteResultsDatabase(db_path=args.db)
        else:
            tmp = tempfile.TemporaryDirectory(prefix="voxbench-")
            backend = SQLiteResultsDatabase(db_path=str(Path(tmp.name) / "results.db"))

    engine = ComputationEngine(backend=backend, max_concurrency=args.threads,
                               progress=args.progress)
    if args.live_refresh and hasattr(engine.config, "live_refresh_interval"):
        import dataclasses  # only meaningful on the pre-frontier engine (ablation)
        engine.config = dataclasses.replace(engine.config, live_refresh_interval=args.live_refresh)
    engine.adopt_plan(plan)
    for goal in plan.goals:
        engine.submit(goal.id, goal.operation, goal.name)

    sampler = Sampler(engine)
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
    # Time to first completion: expansion/startup stall before compute begins.
    first = next((s[0] - t1 for s in sampler.samples if s[1] > 0), wall)
    # Per-5s throughput/busy-core timeline.
    timeline = []
    samples = sampler.samples
    for i in range(5, len(samples), 5):
        (ta, ca, ua), (tb, cb, ub) = samples[i - 5], samples[i]
        timeline.append((round(tb - t1), round((cb - ca) / (tb - ta), 1), round((ub - ua) / (tb - ta), 1)))

    summary = {
        "elements": args.elements, "width": args.width, "rounds": args.rounds,
        "threads": engine.max_concurrency, "cache": not args.no_cache,
        "plan_nodes": len(plan.nodes), "plan_build_s": round(t_plan, 2),
        "total_nodes": len(engine.table.nodes), "completed": completed,
        "wall_s": round(wall, 2), "nodes_per_s": round(completed / wall, 1),
        "first_completion_s": round(first, 2),
        "mean_busy_cores": round(cpu / wall, 1),
        "metrics": engine.metrics(),
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print("\n== bench_scheduler summary ==")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        print("  timeline (t_s, nodes/s, busy_cores):")
        for row in timeline:
            print(f"    {row}")

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
