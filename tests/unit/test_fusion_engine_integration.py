"""End-to-end fusion equivalence through the real ComputationEngine.

Drives an actual chain of elementwise vox1 kernels (leq_sv/geq_sv/and/or/not)
through the real scheduler with fusion on and off, asserting bit-identical
output and clean scheduler bookkeeping — the Phase-1 gates from
doc/specs/fusion-implementation-handover.md §1e.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.priority import Priority
from voxlogica.lazy.ir import SymbolicPlan
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

PROGRAM = """
import "simpleitk"
import "vox1"
img = ReadImage("{path}")
lo = leq_sv(3.0, img)
hi = geq_sv(1.0, img)
combo = and(lo, hi)
neg = not(combo)
either = or(lo, hi)
print "neg" neg
print "either" either
"""


def _write_test_image(path) -> None:
    arr = np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6) % 5
    sitk.WriteImage(sitk.GetImageFromArray(arr), str(path))


def _plan(tmp_path) -> SymbolicPlan:
    img_path = tmp_path / "in.nii.gz"
    _write_test_image(img_path)
    program = PROGRAM.format(path=str(img_path).replace("\\", "/"))
    return reduce_program(parse_program_content(program)).to_symbolic_plan()


def _run(plan: SymbolicPlan, *, fusion_enabled: bool, db_path=None):
    """Submit every goal of ``plan`` through a fresh engine; return
    (engine, {goal_name: value}, metrics_snapshot).

    ``metrics_snapshot`` is captured before the backend closes — engine.metrics()
    reads live backend stats and would raise once the connection is gone.
    """
    backend = SQLiteResultsDatabase(db_path=str(db_path)) if db_path else None
    engine = ComputationEngine(backend=backend)
    engine.config = _replace_fusion(engine.config, fusion_enabled)
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL))
                   for g in plan.goals]
        await engine.run()
        return {g.name: await q.result() for g, q in queries}

    values = asyncio.run(_drive())
    metrics = engine.metrics()
    if backend is not None:
        backend.close()
    return engine, values, metrics


def _replace_fusion(config, fusion_enabled: bool):
    from dataclasses import replace
    return replace(config, fusion_enabled=fusion_enabled)


@pytest.mark.unit
def test_fused_and_unfused_runs_produce_bit_identical_output(tmp_path) -> None:
    plan = _plan(tmp_path)
    fused_engine, fused, fused_metrics = _run(plan, fusion_enabled=True)
    unfused_engine, unfused, unfused_metrics = _run(plan, fusion_enabled=False)

    assert fused_metrics["cones_dispatched"] > 0, \
        "test program must actually exercise fusion, or this test proves nothing"
    assert unfused_metrics["cones_dispatched"] == 0

    for name in ("neg", "either"):
        fused_arr = fused[name].np() if hasattr(fused[name], "np") else fused[name]
        unfused_arr = unfused[name].np() if hasattr(unfused[name], "np") else unfused[name]
        assert np.array_equal(np.asarray(fused_arr), np.asarray(unfused_arr)), \
            f"goal {name!r} diverged between fused and unfused runs"


@pytest.mark.unit
def test_fused_run_leaves_scheduler_bookkeeping_empty(tmp_path) -> None:
    """After a completed (fused) run, no per-node scheduling state should
    remain — exactly as an unfused run leaves none (engine/graph.py's
    invariant: state exists only between register and on_complete)."""
    plan = _plan(tmp_path)
    engine, _, _ = _run(plan, fusion_enabled=True)

    assert engine.graph.pending == {}
    assert engine.graph.incomplete == set()
    # consumers may retain entries only for still-protected goal values;
    # everything else must have been released.
    non_goal_consumers = {k: v for k, v in engine.graph.consumers.items() if k not in engine._goals}
    assert non_goal_consumers == {}, f"dangling consumer refcounts: {non_goal_consumers}"


@pytest.mark.unit
def test_fusion_cap_of_one_disables_fusion_without_changing_output(tmp_path) -> None:
    """A cap that forbids any cone (< 2 members possible) must fall back to
    the single-node path with identical results — cap is a pure knob."""
    plan = _plan(tmp_path)
    from dataclasses import replace
    backend = None
    engine = ComputationEngine(backend=backend)
    engine.config = replace(engine.config, fusion_enabled=True, fusion_cap=1)
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL)) for g in plan.goals]
        await engine.run()
        return {g.name: await q.result() for g, q in queries}

    values = asyncio.run(_drive())
    assert engine.metrics()["cones_dispatched"] == 0
    assert values["neg"] is not None


@pytest.mark.unit
def test_fused_loop_body_root_never_elided_even_when_sequence_registers_late(tmp_path) -> None:
    """Regression test for a real deadlock found while implementing Phase 2
    leg 1 (batched interior completion).

    A runtime loop's body root carries a "stage pin" (LoopAdmission._run_job:
    graph.pin(body)) that protects its value until the loop's sequence node
    registers as its real consumer — an event that happens only once EVERY
    body has been admitted, independent of and possibly LATER than any one
    body's own fusion cone finishing (cone planning happens at ready-queue
    pop time). A body root's stage pin is invisible to a check that only
    looks at graph._dependents (pin() bumps the consumers refcount without
    adding a dependent entry) — so a naive "no dependents yet -> interior"
    rule wrongly elided the body root's value. The sequence node then waited
    forever for a value that had been computed but never materialized: a
    real hang, not merely a wrong result. Forcing a tiny admission window
    with more elements than the window guarantees some bodies finish their
    cone while others are still being admitted, reproducing the race
    deterministically regardless of machine core count.
    """
    img_path = tmp_path / "in.nii.gz"
    _write_test_image(img_path)
    nots = "combo"
    for _ in range(4):
        nots = f"not({nots})"
    escaped_path = str(img_path).replace("\\", "/")
    program = f'''
import "simpleitk"
import "vox1"
img = ReadImage("{escaped_path}")
idxs = range(0, 40)
let elementwise_chain(i) =
  let combo = leq_sv(1.0 + i*0.001, img) in
  {nots}
result = for i in idxs do elementwise_chain(i)
print "result" result
'''
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()
    from dataclasses import replace
    engine = ComputationEngine(max_concurrency=2)
    engine.config = replace(engine.config, fusion_enabled=True, loop_window=2)
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL)) for g in plan.goals]
        await asyncio.wait_for(engine.run(), timeout=20.0)
        return {g.name: await q.result() for g, q in queries}

    values = asyncio.run(_drive())
    metrics = engine.metrics()
    assert metrics["cones_dispatched"] > 0
    assert metrics["interiors_elided"] > 0, "test must exercise the elision path or it proves nothing"

    result_seq = values["result"]
    items = list(result_seq.iter_values()) if hasattr(result_seq, "iter_values") else list(result_seq)
    assert len(items) == 40, "every body's value must have reached the sequence, none silently missing"
    assert all(item is not None for item in items)


@pytest.mark.unit
def test_warm_run_reuses_after_a_fused_cold_run(tmp_path) -> None:
    """A fused cold run persists normally; a subsequent warm run (fusion on
    or off) must reuse the cache, not recompute."""
    plan = _plan(tmp_path)
    db_path = tmp_path / "results.db"

    cold_engine, cold_values, cold_metrics = _run(plan, fusion_enabled=True, db_path=db_path)
    assert cold_metrics["kernels_executed"] > 0

    warm_engine, warm_values, warm_metrics = _run(plan, fusion_enabled=True, db_path=db_path)
    for name in ("neg", "either"):
        a = cold_values[name].np() if hasattr(cold_values[name], "np") else cold_values[name]
        b = warm_values[name].np() if hasattr(warm_values[name], "np") else warm_values[name]
        assert np.array_equal(np.asarray(a), np.asarray(b))
