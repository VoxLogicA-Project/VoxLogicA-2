"""A loop node in the store is not a value the store can serve.

Its payload is a container naming its bodies by hash, and those ids exist only
once the loop has been expanded, so ``NodeTable.load`` refuses it
(``_references_are_answerable``) and ``_rematerialize`` is left with nothing but
``NeedsExpansion``. Measured on a warm-store sweep that died at goal
materialization with 21 of 23 goals, against a store whose ``default.for_loop``
row and all 69 of its elements were intact and un-evicted.

Three invariants, one per way the failure reached the user:
- a persisted loop node is scheduled, not pruned;
- resolution still finds the spliced sequence after the worker's forwarding turn
  has consumed the scheduling alias;
- a waiter is never parked on a loop node that has already completed.
"""

from __future__ import annotations

import contextlib
import io

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

# Arithmetic only: this is about scheduling a loop, not about what it computes.
PROGRAM = """
let xs = for i in range(0, 4) do +(i, 1)
print "first" index(xs, 0)
print "third" index(xs, 2)
"""

EXPECTED = ["first=1.0", "third=3.0"]


def _plan():
    return reduce_program(parse_program_content(PROGRAM))


def _loop_ids(engine: ComputationEngine) -> list[str]:
    return [nid for nid, node in engine.table.nodes.items()
            if node.operator in ("default.for_loop", "for_loop")]


def _loop_id(engine: ComputationEngine) -> str:
    loops = _loop_ids(engine)
    assert loops, "the program must contain a loop node"
    return loops[0]


def _run(db_path) -> list[str]:
    backend = SQLiteResultsDatabase(db_path=str(db_path))
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            ExecutionEngine(storage_backend=backend, use_engine=True).execute_workplan(_plan())
    finally:
        backend.close()
    return sorted(l for l in buffer.getvalue().splitlines() if l.startswith(("first=", "third=")))


@pytest.mark.unit
def test_a_warm_run_reproduces_a_loop_program(tmp_path) -> None:
    """Cold then warm on the same store: same answers, and the warm one runs."""
    db = tmp_path / "results.db"
    assert _run(db) == EXPECTED          # cold: fills the store
    assert _run(db) == EXPECTED          # warm: the loop row is now in it


@pytest.mark.unit
def test_a_persisted_loop_node_is_not_pruned(tmp_path) -> None:
    """`_available` must not promise a loop value the store cannot serve."""
    db = tmp_path / "results.db"
    _run(db)
    backend = SQLiteResultsDatabase(db_path=str(db))
    try:
        engine = ComputationEngine(backend=backend)
        engine.adopt_plan(_plan())
        # The program reduces to several loop nodes; take one the cold run
        # actually computed, so "persisted" is a fact and not an assumption.
        stored = [nid for nid in _loop_ids(engine) if engine.table.persisted(nid)]
        assert stored, "the cold run should have stored at least one loop node"
        loop = stored[0]
        assert engine.table.load(loop) is None, (
            "and the store cannot serve it: its element ids are not interned in this run"
        )
        assert not engine._available(loop), "so it must be scheduled, not pruned"
    finally:
        backend.close()


@pytest.mark.unit
def test_forwarding_outlives_the_scheduling_alias() -> None:
    """The forward is once-only; the alias that made it possible is not.

    Before this, `_worker` popped `_alias` on its forwarding turn, so the loop
    id forgot where its value came from and rematerializing it raised
    NeedsExpansion even while the spliced sequence still held the identical
    payload. `tests/unit/test_store_not_load_bearing.py` pins the same property
    from the other side (no store at all); this one states it in the warm-store
    vocabulary the rest of this file uses.
    """
    engine = ComputationEngine(backend=None)
    engine.adopt_plan(_plan())
    loop = _loop_id(engine)
    seq = next(nid for nid in engine.table.nodes if nid != loop)
    engine._alias[loop] = seq
    engine.table.set_value(seq, [1, 2, 3, 4])

    engine._forwarded.add(loop)  # what `_worker` records once it has forwarded

    assert engine._resolve_reference(loop) == [1, 2, 3, 4]


@pytest.mark.unit
def test_a_waiter_is_never_parked_on_a_completed_loop() -> None:
    """`_await_expansion` on a completed node must requeue, then refuse loudly.

    Registering it would put a completed node back on `incomplete`, `await_one`
    would grant the wait, and the arrival it waits for has already been
    announced: an empty queue that never drains (qsize=0, outstanding=49).
    """
    engine = ComputationEngine(backend=None)
    engine.adopt_plan(_plan())
    loop = _loop_id(engine)
    waiter = next(nid for nid in engine.table.nodes if nid != loop)
    engine.table.completed.add(loop)

    engine._await_expansion(waiter, loop)          # one retry: it may have landed
    assert loop not in engine.graph.incomplete, "a completed node must not be re-registered"
    assert engine.graph.pending.get(waiter, 0) == 0, "and the waiter must not be parked"

    with pytest.raises(RuntimeError, match="grows the graph"):
        engine._await_expansion(waiter, loop)      # second visit: unreachable, say so
