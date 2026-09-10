"""A refused wait must push the waiter, never park it.

`engine/waiting.py` states the rule as invariant 2: "a wakeup is never lost:
`arrived` on an input nobody waits for is a no-op, and `add_wait` after the
arrival is refused rather than silently pending forever -- the caller is told,
and can proceed." `DependencyGraph.await_one` implements the telling by
returning False.

Two of its five call sites dropped that return, on the assumption that their own
`if dep in self.graph.incomplete` test made the refusal impossible. It does not:
`incomplete` membership is removed by `on_complete`, and anything that drives a
completion between the test and the call opens the window -- which draining the
spill queue on every turn (d364ff4) made an ordinary event rather than a rare
one. The waiter then waits for an announcement that has already happened, and if
it is a goal the run ends in

    engine finished with an unresolved goal; this is a scheduling failure,
    not a successful empty result

which is exactly how the sixty-case oracle sweep ended after 1 h 37 min and
2,573,963 completions, on two goals of seven -- both of them
`for g in cases do ...` loop goals, with an empty queue and nothing in flight.

These tests force the refusal (rather than trying to win a race) and assert the
waiter is offered to the scheduler anyway. Forcing it is the right method here:
the property under test is "the return value is honoured", and a test that
depended on hitting the window would be exactly as unreliable as the bug.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.core import ComputationEngine


class _Ready:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, int]] = []

    def push(self, nid, priority):
        self.pushed.append((nid, priority))


class _Graph:
    """A graph that ACCEPTS the membership test and then refuses the wait."""

    def __init__(self, incomplete, refuse: bool) -> None:
        self.incomplete = set(incomplete)
        self._refuse = refuse
        self.pinned: list[str] = []
        self.waits: list[tuple[str, str]] = []
        self.registered: list[str] = []

    def pin(self, nid):
        self.pinned.append(nid)

    def register(self, nid):
        self.registered.append(nid)
        return True

    def await_one(self, nid, dep):
        self.waits.append((nid, dep))
        return not self._refuse


class _Table:
    def __init__(self, completed=()) -> None:
        self.completed = set(completed)


def _engine(graph, table=None) -> ComputationEngine:
    """An engine stub: these two methods are pure scheduling bookkeeping."""
    engine = object.__new__(ComputationEngine)
    engine.graph = graph
    engine.table = table if table is not None else _Table()
    engine.ready = _Ready()
    engine._alias = {}
    engine._priority = {}
    return engine


@pytest.mark.unit
def test_a_spliced_loop_whose_sequence_completed_in_the_window_still_fires() -> None:
    graph = _Graph(incomplete={"seq"}, refuse=True)
    engine = _engine(graph)
    engine._on_spliced("loop", "seq", 5)
    assert graph.waits == [("loop", "seq")], "the wait must still be attempted"
    assert ("loop", 5) in engine.ready.pushed, (
        "the wait was refused and the loop node was not offered to the "
        "scheduler, so nothing will ever fire it again")
    assert engine._alias["loop"] == "seq"


@pytest.mark.unit
def test_a_spliced_loop_that_does_get_its_wait_is_not_double_pushed() -> None:
    """The fix must not turn one wakeup into two: a granted wait is the wakeup."""
    graph = _Graph(incomplete={"seq"}, refuse=False)
    engine = _engine(graph)
    engine._on_spliced("loop", "seq", 5)
    assert graph.waits == [("loop", "seq")]
    assert engine.ready.pushed == [], (
        "a granted wait already fires the node; pushing it too would run it "
        "before its sequence has a value")


@pytest.mark.unit
def test_an_already_complete_sequence_pushes_the_loop_without_waiting() -> None:
    graph = _Graph(incomplete=set(), refuse=True)
    engine = _engine(graph)
    engine._on_spliced("loop", "seq", 3)
    assert graph.waits == [], "there is nothing to wait for"
    assert engine.ready.pushed == [("loop", 3)]


@pytest.mark.unit
def test_a_rewritten_node_whose_target_completed_in_the_window_still_fires() -> None:
    graph = _Graph(incomplete={"tgt"}, refuse=True)
    engine = _engine(graph)
    engine._register_new_subtree = lambda *a, **k: None     # not under test here
    engine._on_rewritten("node", "tgt", 7)
    assert graph.waits == [("node", "tgt")]
    assert ("node", 7) in engine.ready.pushed
    assert engine._alias["node"] == "tgt"


@pytest.mark.unit
def test_a_rewritten_node_whose_unregistered_target_completes_in_the_window() -> None:
    """The third branch: the target was neither incomplete nor completed."""
    graph = _Graph(incomplete=set(), refuse=True)
    engine = _engine(graph, _Table(completed=set()))
    engine._register_new_subtree = lambda *a, **k: None
    engine._on_rewritten("node", "tgt", 2)
    assert graph.registered == ["tgt"]
    assert ("tgt", 2) in engine.ready.pushed, "the target must be scheduled"
    assert ("node", 2) in engine.ready.pushed, (
        "the refused wait left the rewritten node with no path to firing")
