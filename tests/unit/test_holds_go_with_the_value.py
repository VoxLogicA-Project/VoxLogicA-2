"""A value's handle registrations live as long as the value, not as long as
its last consumer.

FOUND BY CLAUSE (H) OF THE INVARIANT CHECKER, on the one configuration the soak
matrix could not get clean: `brats-threshold-sweep-aiim.imgql --no-cache`,
deterministic at 8 and 24 threads, 13 of 16 goals. The check named the producer
of every offending value in a single run -- `op='default.for_loop'` -- which is
the goal node of `print "..." for g in cases do ...`, and goal ids are exactly
the `protected` ones.

THE DEFECT. `DependencyGraph.release`, on the last consumer:

    if nid not in self.protected:
        self.table.evict(nid)          # skipped for every goal
    for ref in self._handle_refs.pop(nid, ()):   # done anyway
        self.release(ref)

So a goal's value stayed RESIDENT while its registrations were dropped. Two
consequences, and the second is a correctness bug rather than a crash:

  1. `names_handles` began answering "no" for a value full of handles, so
     `_eager` skipped resolution and handed an EAGER kernel raw `Handle`
     objects. Measured as `default.argmax` comparing two of them --
     `TypeError: '>' not supported between instances of 'Handle' and 'Handle'`
     -- seven frames deep, surfacing as "Invalid operation input".
  2. The nodes those handles named were released, so they could be evicted
     while a live value still referred to them. That is the "reference
     outliving what it refers to" failure `release`'s own docstring warns
     about, committed by the code underneath it.

`--no-cache` is where it is fatal: with a disk tier the prematurely released
elements are reloaded and the damage is invisible. That is why this survived so
long, and why the equivalence configurations are in the soak matrix.

THE RULE, now: the holds are released exactly when the value is gone. `evict`
can also DECLINE -- no disk tier and the value not recomputable -- which leaves
it resident for a different reason and wants the same answer, so the test is
the value's absence rather than the branch taken.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.graph import DependencyGraph
from voxlogica.engine.node_table import NodeTable
from voxlogica.handles import Handle
from voxlogica.lazy.ir import NodeSpec


def _graph() -> tuple[DependencyGraph, NodeTable]:
    table = NodeTable(backend=None)
    return DependencyGraph(table), table


@pytest.mark.unit
def test_a_protected_value_keeps_its_handle_registrations() -> None:
    """The goal case, which is the measured one."""
    graph, table = _graph()
    goal, element = "g" * 64, "e" * 64
    table.nodes[goal] = NodeSpec(kind="primitive", operator="default.for_loop")
    table.nodes[element] = NodeSpec(kind="primitive", operator="test.blob")

    table.set_value(element, 1.0)
    table.set_value(goal, [Handle(node=element)])
    graph.hold_handles(goal, table.values[goal])
    graph.protected.add(goal)                 # what `submit` does for a goal
    graph.consumers[goal] = 1
    assert graph.names_handles(goal)

    graph.release(goal)                       # its last consumer runs

    assert goal in table.values, "a protected value must not be evicted"
    assert graph.names_handles(goal), (
        "the registrations were dropped while the value is still resident: "
        "`_eager` will now hand an eager kernel raw Handle objects")


@pytest.mark.unit
def test_a_protected_value_keeps_what_its_handles_name_alive() -> None:
    """The correctness half: a live value must not point at an evicted one."""
    graph, table = _graph()
    goal, element = "h" * 64, "f" * 64
    table.nodes[goal] = NodeSpec(kind="primitive", operator="default.for_loop")
    table.nodes[element] = NodeSpec(kind="primitive", operator="test.blob")

    table.set_value(element, 2.0)
    table.set_value(goal, [Handle(node=element)])
    graph.hold_handles(goal, table.values[goal])
    graph.protected.add(goal)
    graph.consumers[goal] = 1

    held_before = graph.consumers.get(element, 0)
    assert held_before > 0, "hold_handles must count the handle as a reference"
    graph.release(goal)
    assert graph.consumers.get(element, 0) == held_before, (
        "the element was released while a resident value still names it -- a "
        "reference outliving what it refers to")


@pytest.mark.unit
def test_an_ordinary_value_still_releases_everything_it_named() -> None:
    """The fix must not leak: an evicted holder gives up its holds as before."""
    graph, table = _graph()
    holder, element = "i" * 64, "j" * 64
    table.nodes[holder] = NodeSpec(kind="primitive", operator="default.sequence")
    table.nodes[element] = NodeSpec(kind="primitive", operator="test.blob")

    table.set_value(element, 3.0)
    table.set_value(holder, [Handle(node=element)])
    graph.hold_handles(holder, table.values[holder])
    graph.consumers[holder] = 1
    assert graph.consumers.get(element, 0) == 1

    graph.release(holder)                     # not protected: evicted

    assert holder not in table.values, "an unprotected value must be evicted"
    assert not graph.names_handles(holder), "its registration must be gone"
    assert graph.consumers.get(element, 0) == 0, (
        "the element is still held by a value that no longer exists")


@pytest.mark.unit
def test_holds_survive_an_eviction_that_was_declined() -> None:
    """`evict` can refuse, and a refused eviction wants the same answer.

    With no disk tier a loop or sequence value has no kernel to come back
    from, so `NodeTable.evict` holds it rather than losing it. It is then
    resident for a different reason and its handles must still be registered.
    """
    graph, table = _graph()
    holder, element = "k" * 64, "l" * 64
    table.nodes[holder] = NodeSpec(kind="primitive", operator="default.sequence")
    table.nodes[element] = NodeSpec(kind="primitive", operator="test.blob")
    table.set_value(element, 4.0)
    table.set_value(holder, [Handle(node=element)])
    graph.hold_handles(holder, table.values[holder])
    graph.consumers[holder] = 1
    # The guard `NodeTable.evict` consults with no backend: "not recomputable".
    table._recompute_guard = lambda nid: False

    graph.release(holder)

    if holder in table.values:                # the eviction was declined
        assert graph.names_handles(holder), (
            "a declined eviction left the value resident but unregistered")
        assert graph.consumers.get(element, 0) > 0
