"""A rewriter that meets an unexpanded loop must schedule it, not fail the node.

FOUND BY THE SOAK MATRIX, in two seconds, on a ten-line program, in six of six
warm configurations -- after the same class of failure had cost three sweeps of
between 27 seconds and 84 minutes to characterise:

    NodeExecutionError: default.fold failed while evaluating node ccbe9fbe1def
    NeedsExpansion: af2ee8d9519e... must be expanded, not computed
      [operator='default.for_loop' interned=True completed=False alias=False
       forwarded=False incomplete=False persisted=True]
    core.py in _worker: elif (target := self._rewrite_of(node)) is not None

THE MECHANISM, and why only a warm store shows it. `fold` is a rewriter: it
does not compute, it names the node it becomes, and to do that it RESOLVES the
sequence it folds. On a cold run that sequence's loop node was expanded on the
way in, so the resolution hits a value. On a warm run the loop node is
`persisted`, `_schedule_subgraph` prunes it as available, no expansion is ever
scheduled -- and the resolution raises into `_worker`'s if/elif chain, where
nothing caught it.

The fix is the same recovery the other three `NeedsExpansion` sites already
perform, because this one is also on the loop thread: register the node so
admission expands it, and requeue the waiter. `_rewrite_of` returns the miss
instead of raising, so the caller can act on it; raising made it a node
failure, which it is not -- `NeedsExpansion` is "an outcome, not a failure: the
value is still obtainable, just not by the road the caller took".

The end-to-end test runs the program TWICE against one store, because the
second pass is the whole point: the first pass is what makes the store warm.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.evaluation import NeedsExpansion

_PROGRAM = '''import "test"
let n = 8
print "total"  fold + (for i in range(0, n) do spin(i, 20))
'''


@pytest.mark.unit
def test_rewrite_of_returns_the_miss_instead_of_raising() -> None:
    """The unit-level property: a rewriter's miss is data, not an exception."""
    engine = object.__new__(ComputationEngine)
    engine.registry = None
    engine.table = type("T", (), {"intern": staticmethod(lambda *a: None)})()
    engine._resolve_reference = lambda nid: None

    node = type("N", (), {"operator": "default.fold", "args": (), "kwargs": ()})()
    loop_id = "a" * 64

    def rewriter(spec, ctx):
        raise NeedsExpansion(loop_id, "operator='default.for_loop'")

    import voxlogica.engine.core as core_mod
    real_modes = core_mod.modes_of
    # staticmethod, or attribute access on the instance binds it and the
    # rewriter is handed `self` as its first argument.
    core_mod.modes_of = lambda registry, op: type(
        "M", (), {"rewriter": staticmethod(rewriter), "rewrite": True,
                  "lazy": False, "shallow": False})()
    try:
        got = engine._rewrite_of(node)
    finally:
        core_mod.modes_of = real_modes

    assert isinstance(got, NeedsExpansion), (
        "the rewriter's miss propagated as an exception; in `_worker`'s "
        "if/elif chain that becomes a node failure and kills the run")
    assert got.node_id == loop_id, "the caller cannot schedule what it is not told"


@pytest.mark.unit
def test_rewrite_of_still_returns_none_and_a_target_normally() -> None:
    """The fix must not swallow the two ordinary answers."""
    import voxlogica.engine.core as core_mod

    engine = object.__new__(ComputationEngine)
    engine.registry = None
    engine.table = type("T", (), {"intern": staticmethod(lambda *a: None)})()
    engine._resolve_reference = lambda nid: None
    node = type("N", (), {"operator": "default.fold"})()

    real_modes = core_mod.modes_of
    try:
        core_mod.modes_of = lambda r, o: type("M", (), {"rewriter": None})()
        assert engine._rewrite_of(node) is None, "no rewriter must mean None"
        core_mod.modes_of = lambda r, o: type(
            "M", (), {"rewriter": staticmethod(lambda spec, ctx: "b" * 64)})()
        assert engine._rewrite_of(node) == "b" * 64
    finally:
        core_mod.modes_of = real_modes


@pytest.mark.e2e
def test_a_warm_store_run_of_a_fold_over_a_computed_loop_completes(tmp_path) -> None:
    """The reproduction itself: the SECOND pass is the one that used to die."""
    program = tmp_path / "warmfold.imgql"
    program.write_text(_PROGRAM)
    store = tmp_path / "warm.db"

    for attempt in (1, 2):
        result = subprocess.run(
            [sys.executable, "-m", "voxlogica.main", "run", "--no-serve",
             "--threads", "4", "--sparse-cache", "--store-db", str(store),
             "--verify=strict", "--error-details", str(program)],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=600)
        assert result.returncode == 0, (
            f"pass {attempt} failed (this is the warm path if attempt==2):\n"
            f"{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
        assert "NeedsExpansion" not in result.stdout + result.stderr
        assert "[verify" not in result.stderr, (
            f"the invariant checker objected:\n{result.stderr[-2000:]}")
