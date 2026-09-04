#!/usr/bin/env python3
"""Stage A vs Stage B (numba-compiled cones) throughput on a realistic
elementwise chain.

Each ``EngineExecutionStrategy.run()`` (one per CLI/server query — see
``engine/strategy.py``) creates a FRESH ``ComputationEngine``, and hence a
fresh, empty ``NumbaFusionBackend``: the compile cache is scoped to one
engine's lifetime, never shared across separate queries in the same process
(a deliberate scope boundary, not a bug — see numba_fusion.py's module
docstring on why there is no cross-process disk cache). So the realistic
unit to benchmark is ONE run with enough repeats of the same cone shape for
compilation to land mid-flight and the LATER repeats to take Stage B —
exactly "a runtime loop's thousands of structurally-identical per-element
cones ... all warm exactly one compile" (numba_fusion.py).

Compares:
  1. numba disabled entirely            (Stage A only, pre-Phase-2-leg-2 baseline)
  2. numba enabled, one run, N iters     (mix of Stage A early, Stage B once compiled)

Usage:
  python tests/perf/bench_numba_fusion.py --size 128 --iters 400
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "implementation" / "python"))


def build_program(img_path: str, iters: int) -> str:
    # A 6-op elementwise cone (leq_sv, geq_sv, and, not, or, between), swept
    # over `iters` distinct thresholds so cone SHAPE repeats (one compile)
    # while the constant differs per element — exactly the pattern Stage B
    # targets (see numba_fusion.py's ConeShape docstring).
    return f'''
import "simpleitk"
import "vox1"
img = ReadImage("{img_path}")
idxs = range(0, {iters})
let chain(i) =
  let lo = leq_sv(100.0 + i, img) in
  let hi = geq_sv(10.0 + i, img) in
  let combo = and(lo, hi) in
  let neg = not(combo) in
  let either = or(lo, hi) in
  let btw = between(10.0 + i, 100.0 + i, img) in
  or(or(neg, btw), either)
result = for i in idxs do chain(i)
print "result" result
'''


def _run(plan, *, fusion_enabled: bool, numba_enabled: bool):
    import asyncio

    from voxlogica.engine.core import ComputationEngine
    from voxlogica.engine.priority import Priority

    engine = ComputationEngine()
    engine.config = replace(engine.config, fusion_enabled=fusion_enabled,
                            numba_fusion_enabled=numba_enabled)
    if not numba_enabled:
        engine.numba_backend = None
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL)) for g in plan.goals]
        await engine.run()
        return {g.name: await q.result() for g, q in queries}

    t0 = time.perf_counter()
    asyncio.run(_drive())
    wall = time.perf_counter() - t0
    return engine, wall


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", type=int, default=128, help="cube side length (voxels)")
    parser.add_argument("--iters", type=int, default=400, help="loop elements (cone repeats)")
    args = parser.parse_args()

    import numpy as np
    import SimpleITK as sitk

    from voxlogica.parser import parse_program_content
    from voxlogica.reducer import reduce_program

    img_path = "/tmp/voxlogica-numba-bench.mha"
    rng = np.random.default_rng(0)
    arr = rng.uniform(0, 200, size=(args.size,) * 3).astype(np.float32)
    sitk.WriteImage(sitk.GetImageFromArray(arr), img_path)

    program = build_program(img_path, args.iters)
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()

    _, wall_a = _run(plan, fusion_enabled=True, numba_enabled=False)
    print(f"Stage A only (numba disabled):   {wall_a:.3f}s")

    engine_b, wall_b = _run(plan, fusion_enabled=True, numba_enabled=True)
    m = engine_b.metrics()
    print(f"Stage A+B (one run, {args.iters} iters): {wall_b:.3f}s")
    print(f"  cones_dispatched={m['cones_dispatched']} cones_numba={m['cones_numba']} "
          f"({100 * m['cones_numba'] / max(1, m['cones_dispatched']):.0f}% took Stage B)")
    print(f"  compiles: started={m.get('numba_compiles_started')} "
          f"finished={m.get('numba_compiles_finished')} failed={m.get('numba_compiles_failed')}")
    print(f"speedup vs Stage A only: {wall_a / wall_b:.2f}x")


if __name__ == "__main__":
    main()
