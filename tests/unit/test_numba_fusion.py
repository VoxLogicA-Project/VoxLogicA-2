"""Stage B (numba-compiled cones) — bit-identical vs Stage A, through the
real ComputationEngine.

Background compilation means the first run of any given cone shape always
executes Stage A (see ``NumbaFusionBackend.try_get``); this module waits for
compilation to land, then drives a second run and asserts it actually took
the Stage B path with identical output.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.priority import Priority
from voxlogica.lazy.ir import SymbolicPlan
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

PROGRAM = """
import "simpleitk"
import "vox1"
img = ReadImage("{path}")
lo = leq_sv(3.0, img)
hi = geq_sv(1.0, img)
combo = and(lo, hi)
neg = not(combo)
either = or(lo, hi)
btw = between(1.0, 3.0, img)
print "neg" neg
print "either" either
print "btw" btw
"""


def _write_test_image(path) -> None:
    arr = np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6) % 5
    sitk.WriteImage(sitk.GetImageFromArray(arr), str(path))


def _plan(tmp_path) -> SymbolicPlan:
    img_path = tmp_path / "in.nii.gz"
    _write_test_image(img_path)
    program = PROGRAM.format(path=str(img_path).replace("\\", "/"))
    return reduce_program(parse_program_content(program)).to_symbolic_plan()


def _run(plan: SymbolicPlan, *, numba_backend=None):
    engine = ComputationEngine()
    engine.config = replace(engine.config, fusion_enabled=True)
    if numba_backend is not None:
        engine.numba_backend = numba_backend
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL))
                   for g in plan.goals]
        await engine.run()
        return {g.name: await q.result() for g, q in queries}

    values = asyncio.run(_drive())
    return engine, values, engine.metrics()


@pytest.mark.unit
def test_stage_b_matches_stage_a_once_compiled(tmp_path) -> None:
    plan = _plan(tmp_path)

    # Cold run: no shape is compiled yet, so this run is pure Stage A but
    # kicks off background compiles for every cone shape it sees.
    cold_engine, cold_values, cold_metrics = _run(plan)
    assert cold_metrics["cones_dispatched"] > 0
    assert cold_metrics["cones_numba"] == 0, \
        "first-ever run of a shape must never block on its own compile"

    backend = cold_engine.numba_backend
    deadline = time.monotonic() + 10.0
    while backend.compiles_finished + backend.compiles_failed < backend.compiles_started:
        if time.monotonic() > deadline:
            pytest.fail("background numba compile(s) never finished")
        time.sleep(0.05)
    assert backend.compiles_failed == 0, "no cone shape in this program should fail to compile"
    assert backend.compiles_finished > 0

    # Warm run reusing the now-populated compile cache: must take Stage B.
    warm_engine, warm_values, warm_metrics = _run(plan, numba_backend=backend)
    assert warm_metrics["cones_numba"] > 0, \
        "test must actually exercise Stage B, or it proves nothing"

    for name in ("neg", "either", "btw"):
        a = cold_values[name].np() if hasattr(cold_values[name], "np") else cold_values[name]
        b = warm_values[name].np() if hasattr(warm_values[name], "np") else warm_values[name]
        assert np.array_equal(np.asarray(a), np.asarray(b)), \
            f"goal {name!r} diverged between Stage A and Stage B"


@pytest.mark.unit
def test_numba_fusion_disabled_never_dispatches_stage_b(tmp_path) -> None:
    plan = _plan(tmp_path)
    engine = ComputationEngine()
    engine.config = replace(engine.config, fusion_enabled=True, numba_fusion_enabled=False)
    engine.numba_backend = None
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL)) for g in plan.goals]
        await engine.run()
        return {g.name: await q.result() for g, q in queries}

    asyncio.run(_drive())
    metrics = engine.metrics()
    assert metrics["cones_dispatched"] > 0
    assert metrics["cones_numba"] == 0
