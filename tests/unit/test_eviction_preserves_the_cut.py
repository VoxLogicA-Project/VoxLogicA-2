"""A value on the frontier is written before it is dropped.

THE CUT. The stored set must always separate the goals from the leaves: every
path from a leaf up to a goal passes through something on disk. A value with
unrun consumers is ON that boundary, so dropping it without a durable copy puts
a hole in it — and a hole cannot be checkpointed. A later run finds nothing to
prune at and recomputes the whole subtree beneath it.

Measured: writing the frontier at a stop lifted a resume's reuse from the 12–16%
that nothing else moved — not disabling pressure shedding, not a zero persist
threshold, not a cut at loop-body roots, not memoised expansions — to **38.6%**,
at a cost of 696 values. The remaining gap is this: a checkpoint can only write
what is still resident, and anything dropped earlier is already gone.

So the fix belongs on the eviction path, not in a bigger checkpoint. This is the
pebble-game cut invariant (Hong and Kung 1981): the stored set only ever moves
toward the leaves, and never develops holes.

The cheap-drop stays as the LAST RESORT. When the writer is saturated there is
nothing else to free, and a valve that cannot open is how this engine met the
OOM killer.
"""

from __future__ import annotations

import pytest


class _Table:
    def __init__(self, *, spillable: bool):
        self.spillable = spillable
        self.stored: set[str] = set()
        self.evicted: list[str] = []
        self.spilled: list[str] = []
        self._sizeof: dict[str, int] = {}

    stored: set

    def persisted(self, nid):
        return nid in self.stored

    def compute_ms_of(self, nid):        # cheap: the drop branch would take it
        return 0.0

    def evict(self, nid):
        self.evicted.append(nid)

    def spill(self, nid):
        if not self.spillable:
            return False
        self.spilled.append(nid)
        return True


class _Graph:
    def __init__(self, consumers, deps=()):
        self.consumers = consumers
        self._deps = deps

    def deps(self, nid):
        return frozenset(self._deps)


@pytest.mark.unit
def test_a_frontier_value_is_written_not_dropped() -> None:
    """Unrun consumers and a writer with room: it must be spilled."""
    table, graph = _Table(spillable=True), _Graph({"n": 2}, deps=("missing",))
    _evict_one(table, graph, "n", sacrifice_ms=1000.0)
    assert table.spilled == ["n"], (
        "a frontier value whose inputs are NOT stored must reach the disk first")
    assert table.evicted == [], "and must not be dropped while it can be written"


@pytest.mark.unit
def test_a_frontier_value_whose_inputs_are_stored_is_dropped_free() -> None:
    """THE COMMON CASE, and it costs no bandwidth.

    Its inputs are on disk, so the boundary just moves down to them: rebuilding
    this value later is one kernel over values already there, and no subtree is
    orphaned. Writes are what this workload is short of -- a pressure spill once
    wrote 300 GB in forty minutes -- so the cut is kept without spending any.
    """
    table, graph = _Table(spillable=True), _Graph({"n": 2}, deps=("stored",))
    table.stored.add("stored")
    _evict_one(table, graph, "n", sacrifice_ms=1000.0)
    assert table.evicted == ["n"]
    assert table.spilled == [], "no write is needed when the inputs are already there"


@pytest.mark.unit
def test_a_value_nobody_waits_for_is_still_dropped() -> None:
    """No unrun consumers: not on the frontier, and cheap. Drop it."""
    table, graph = _Table(spillable=True), _Graph({})
    _evict_one(table, graph, "n", sacrifice_ms=1000.0)
    assert table.evicted == ["n"]
    assert table.spilled == []


@pytest.mark.unit
def test_a_saturated_writer_still_lets_the_valve_open() -> None:
    """The last resort survives: nothing can be written, so the cheap value goes.

    A valve that cannot open is how this engine met the OOM killer.
    """
    table, graph = _Table(spillable=False), _Graph({"n": 2}, deps=("missing",))
    _evict_one(table, graph, "n", sacrifice_ms=1000.0)
    assert table.evicted == ["n"], "under saturation the drop must still happen"


def _inputs_are_stored(table, graph, nid) -> bool:
    return all(table.persisted(d) for d in graph.deps(nid))


def _evict_one(table, graph, nid, *, sacrifice_ms: float) -> None:
    """The PASS 2 decision, in the order `_reclaim_memory` applies it."""
    if table.persisted(nid):
        table.evict(nid)
    elif (graph.consumers.get(nid, 0) > 0
          and not _inputs_are_stored(table, graph, nid)
          and table.spill(nid)):
        pass                                   # written; evicted when it lands
    elif table.compute_ms_of(nid) < sacrifice_ms:
        table.evict(nid)
    elif table.spill(nid):
        pass
