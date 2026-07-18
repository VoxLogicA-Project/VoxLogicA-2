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

# "neg"'s cone is JUST "and" + N chained "not"s = N+1 members, not the full
# chain it looks like: "lo"/"hi" each complete as their OWN independent
# dispatch (both depend only on "img", so both are ready/completed before
# "and" ever becomes ready) — FusionPlanner only grows a cone FORWARD from
# its seed toward consumers, it never retroactively absorbs an
# already-completed producer. N=13 clears NumbaFusionBackend's
# _MIN_MEMBERS_FOR_STAGE_B gate (12) with margin; below that gate Stage B is
# never even attempted (see numba_fusion.py).
_NOT_DEPTH = 13
PROGRAM = """
import "simpleitk"
import "vox1"
img = ReadImage("{path}")
lo = leq_sv(3.0, img)
hi = geq_sv(1.0, img)
combo = and(lo, hi)
neg = %s
either = or(leq_sv(4.0, img), geq_sv(0.0, img))
btw = between(1.0, 3.0, img)
print "neg" neg
print "either" either
print "btw" btw
""" % ("not(" * _NOT_DEPTH + "combo" + ")" * _NOT_DEPTH)


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


# leq_sv + not*13 = 14 members: clears NumbaFusionBackend's minimum cone
# size (12) with margin (below the gate, Stage B is never even attempted —
# see numba_fusion.py). Single-producer chain (leq_sv is the seed itself,
# no independently-completing sibling to exclude), so all N+1 ops fuse.
_LOOP_PROGRAM = '''
import "simpleitk"
import "vox1"
img = ReadImage("{path}")
idxs = range(0, 40)
let elementwise_chain(i) =
  let combo = leq_sv(1.0 + i*0.001, img) in
  %s
result = for i in idxs do elementwise_chain(i)
print "result" result
''' % ("not(" * _NOT_DEPTH + "combo" + ")" * _NOT_DEPTH)


def _run_loop(tmp_path, *, numba_backend=None, max_concurrency=2, loop_window=2):
    img_path = tmp_path / "in.nii.gz"
    _write_test_image(img_path)
    program = _LOOP_PROGRAM.format(path=str(img_path).replace("\\", "/"))
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()
    engine = ComputationEngine(max_concurrency=max_concurrency)
    engine.config = replace(engine.config, fusion_enabled=True, loop_window=loop_window)
    if numba_backend is not None:
        engine.numba_backend = numba_backend
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL)) for g in plan.goals]
        await asyncio.wait_for(engine.run(), timeout=20.0)
        return {g.name: await q.result() for g, q in queries}

    values = asyncio.run(_drive())
    return engine, values, engine.metrics()


@pytest.mark.unit
def test_stage_b_elided_interior_rematerializes_correctly_under_late_hash_consing(tmp_path) -> None:
    """The design doc's "residual hole" (semantic-queueing-fusion.md §3.2):
    a runtime loop's body root is elided as a cone interior, then later
    demanded by the sequence node once every body is admitted — possibly
    much later, and possibly by a DIFFERENT run than the one that computed
    it. ``_rematerialize``'s generic recompute fallback is what closes this
    gap (see ``test_fused_loop_body_root_never_elided_even_when_sequence_registers_late``
    in test_fusion_engine_integration.py for the Stage A version); this
    confirms the same guarantee holds once Stage B is actually taking the
    dispatch (not just compiling in the background, as in a cold run).
    """
    # Cold run: pure Stage A (no shape compiled yet), just to warm the compile
    # cache for the loop body's cone shape (leq_sv + four chained nots).
    cold_engine, cold_values, cold_metrics = _run_loop(tmp_path)
    backend = cold_engine.numba_backend
    deadline = time.monotonic() + 10.0
    while backend.compiles_finished + backend.compiles_failed < backend.compiles_started:
        if time.monotonic() > deadline:
            pytest.fail("background numba compile(s) never finished")
        time.sleep(0.05)
    assert backend.compiles_failed == 0
    assert backend.compiles_finished > 0

    # Warm run reusing the compiled shape: same tiny admission window forces
    # some bodies' cones to finish (and get elided as interiors) while others
    # are still being admitted — the exact race the residual hole describes.
    warm_engine, warm_values, warm_metrics = _run_loop(tmp_path, numba_backend=backend)
    assert warm_metrics["cones_numba"] > 0, \
        "test must actually exercise Stage B, or it proves nothing"
    assert warm_metrics["interiors_elided"] > 0, \
        "test must actually exercise elision, or it proves nothing"

    def _items(seq):
        return list(seq.iter_values()) if hasattr(seq, "iter_values") else list(seq)

    cold_items = _items(cold_values["result"])
    warm_items = _items(warm_values["result"])
    assert len(warm_items) == 40, "every body's value must reach the sequence, none silently missing"
    assert all(item is not None for item in warm_items)
    def _as_array(item):
        if hasattr(item, "np"):
            return np.asarray(item.np())
        if isinstance(item, sitk.Image):
            return sitk.GetArrayFromImage(item)
        return np.asarray(item)

    for i, (a, b) in enumerate(zip(cold_items, warm_items)):
        assert np.array_equal(_as_array(a), _as_array(b)), \
            f"loop element {i} diverged between Stage A and Stage B"


def _write_edge_case_image(path) -> None:
    """NaN plus values sitting exactly on the leq_sv/geq_sv/between thresholds
    (1.0, 3.0) — the two places a comparison expr fragment could plausibly
    diverge from its real sitk kernel: NaN-propagation and boundary inclusion.

    Written as .mha, NOT .nii.gz: NIfTI silently sanitizes NaN/inf to 0.0 on
    write (confirmed empirically), so a .nii.gz round trip would test
    nothing here. .mha preserves them exactly.
    """
    arr = np.array([np.nan, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, np.inf], dtype=np.float32)
    arr = np.tile(arr, 4 * 5 * 6 // len(arr) + 1)[: 4 * 5 * 6].reshape(4, 5, 6)
    sitk.WriteImage(sitk.GetImageFromArray(arr), str(path))


@pytest.mark.unit
def test_stage_b_matches_stage_a_on_nan_and_boundary_values(tmp_path) -> None:
    """Stage B's expr fragments (numba_fusion.py's ``_expr_for``) must handle
    NaN and exact-threshold values identically to the real sitk kernels they
    were validated against — this is the actual bit-identical CONTRACT, not
    just the happy-path arithmetic the other tests exercise."""
    img_path = tmp_path / "in.mha"
    _write_edge_case_image(img_path)
    program = PROGRAM.format(path=str(img_path).replace("\\", "/"))
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()

    cold_engine, cold_values, _ = _run(plan)
    backend = cold_engine.numba_backend
    deadline = time.monotonic() + 10.0
    while backend.compiles_finished + backend.compiles_failed < backend.compiles_started:
        if time.monotonic() > deadline:
            pytest.fail("background numba compile(s) never finished")
        time.sleep(0.05)
    assert backend.compiles_failed == 0

    warm_engine, warm_values, warm_metrics = _run(plan, numba_backend=backend)
    assert warm_metrics["cones_numba"] > 0, \
        "test must actually exercise Stage B, or it proves nothing"

    for name in ("neg", "either", "btw"):
        a = np.asarray(cold_values[name].np())
        b = np.asarray(warm_values[name].np())
        assert np.array_equal(a, b), \
            f"goal {name!r} diverged on NaN/boundary input between Stage A and Stage B"

    # A known pixel, checked directly (insurance against Stage A and Stage B
    # being coincidentally, identically wrong): NaN must compare false both
    # ways (never "leq" nor "geq" 1.0/3.0), and 1.0/3.0 are inclusive bounds.
    flat_hi = np.asarray(warm_values["either"].np()).reshape(-1)
    assert flat_hi[0] == 0, "NaN must satisfy neither leq_sv(3.0, .) nor geq_sv(1.0, .)"
    flat_btw = np.asarray(warm_values["btw"].np()).reshape(-1)
    assert flat_btw[3] == 1 and flat_btw[5] == 1, "between(1.0, 3.0, .) must include both endpoints"


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
