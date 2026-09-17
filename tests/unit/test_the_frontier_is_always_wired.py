"""A node on the frontier must always have a pending count.

THE FAILURE THIS PREVENTS. `graph.register` is what gives a node its count of
unmet dependencies, and every wakeup path in the engine works by decrementing
that count. `_schedule_subgraph` adds a node to `graph.incomplete` during
discovery and registers it in a later pass, so for as long as those two things
are interleaved there is a window where a node sits on the frontier waiting on
a counter that does not exist. Nothing can ever fire it. The run drains around
it and reports an unresolved goal -- hours later, and nowhere near the cause.

Measured on the BraTS double sweep, 2026-09-17: drained after 2 h 04 m with
`in_flight=0 ready=0 parked=0` and 458 nodes on the frontier, every sampled one
reporting `pending=None`. The same ending appears in `o60_cold2.log`
(2026-09-11), `oracle60.prev.log` and `trainprobe.log`, so it long predates the
resume work of this branch.

Two tests: the scheduler keeps the invariant even when `_enqueue` fails, and the
verifier reports a violation if anything ever reopens the window.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.verify import Verifier
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

PROGRAM = 'import "geom"\nimport "arrays"\nprint "r" array_stats(blank(8, 8, 1.0))\n'


def _engine() -> ComputationEngine:
    engine = ComputationEngine(backend=None)
    engine.adopt_plan(reduce_program(parse_program_content(PROGRAM)))
    return engine


@pytest.mark.unit
def test_a_failing_enqueue_does_not_strand_the_frontier() -> None:
    """Registration must complete for every discovered node even if the
    workers cannot be offered any of them.

    The scheduler used to register and enqueue in one loop, so the first raise
    left every later node on the frontier unregistered and unfireable.
    """
    pytest.importorskip("SimpleITK")
    engine = _engine()

    def _refuse(_nid):
        raise RuntimeError("the ready queue is unavailable")

    engine._enqueue = _refuse
    goal = engine.plan.goals[0]
    engine.submit(goal.id, goal.operation, goal.name)   # must not raise

    stranded = engine.graph.incomplete - engine.graph.pending.keys()
    assert not stranded, (
        f"{len(stranded)} nodes are on the frontier with no pending count; "
        "nothing can ever fire them")
    assert engine._enqueue_failures > 0, "the failure was not even counted"


@pytest.mark.unit
def test_the_verifier_reports_an_unwired_frontier_node() -> None:
    """(R) must catch the state, so reopening the window is loud and located."""
    pytest.importorskip("SimpleITK")
    engine = _engine()
    goal = engine.plan.goals[0]
    engine.submit(goal.id, goal.operation, goal.name)

    verifier = Verifier(engine)
    assert verifier.check_registration() == [], "a healthy frontier must be quiet"

    # Reopen the window by hand: on the frontier, never registered.
    engine.graph.incomplete.add("f" * 64)
    found = verifier.check_registration()
    assert [v.node for v in found] == ["f" * 64]
    assert found[0].clause == "R"
