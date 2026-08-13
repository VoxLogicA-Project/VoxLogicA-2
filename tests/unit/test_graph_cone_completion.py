"""A fusion cone completes its members out of dependency order. Survive it.

`complete_cone` drops the scheduling state of a cone's INTERIOR members in one
batch, and only afterwards does the caller finish the cone's EXITS through the
normal `on_complete` path. An exit is defined as a member with a consumer
*outside* the cone -- which does not stop it from also having consumers inside,
and those consumers can be interiors. So by the time an exit completes, an
interior that depends on it has already had its `pending` entry dropped.

That decrement used to raise KeyError and abort the whole run: a 369-case sweep
died two minutes in with `vox1.eq_sv failed ... KeyError`. Which cones the
planner forms depends on scheduling, so the crash came and went with memory
pressure -- these tests pin the graph invariant directly instead.
"""

import pytest

from voxlogica.engine.graph import DependencyGraph
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeSpec


def _graph_with(specs: dict[str, NodeSpec]) -> DependencyGraph:
    table = NodeTable(backend=None)
    table.nodes.update(specs)
    return DependencyGraph(table)


@pytest.mark.unit
def test_completing_an_exit_after_its_interior_consumer_does_not_raise():
    """The exact shape that killed the sweep: interior --consumes--> exit."""
    graph = _graph_with({
        "exit": NodeSpec(kind="primitive", operator="vox1.eq_sv", args=()),
        "interior": NodeSpec(kind="primitive", operator="vox1.and", args=("exit",)),
    })
    assert graph.register("exit") is True
    assert graph.register("interior") is False, "waits on the exit"

    # The cone batch drops the interior's scheduling state FIRST.
    graph.complete_cone(["interior"], frozenset({"exit", "interior"}),
                        frozenset({"interior"}))
    assert "interior" not in graph.pending

    # Then the exit is finished the normal way. It must not trip over the
    # dependent whose state the batch already removed.
    assert graph.on_complete("exit") == []


@pytest.mark.unit
def test_a_still_pending_sibling_still_fires():
    """Skipping completed children must not swallow a genuine firing."""
    graph = _graph_with({
        "exit": NodeSpec(kind="primitive", operator="vox1.eq_sv", args=()),
        "interior": NodeSpec(kind="primitive", operator="vox1.and", args=("exit",)),
        "outside": NodeSpec(kind="primitive", operator="vox1.or", args=("exit",)),
    })
    graph.register("exit")
    graph.register("interior")
    graph.register("outside")

    graph.complete_cone(["interior"], frozenset({"exit", "interior"}),
                        frozenset({"interior"}))
    assert graph.on_complete("exit") == ["outside"], "the live consumer must fire"


@pytest.mark.unit
def test_ordinary_completion_is_unchanged():
    """No cone in sight: the plain two-dependency firing rule still holds."""
    graph = _graph_with({
        "a": NodeSpec(kind="primitive", operator="vox1.dt", args=()),
        "b": NodeSpec(kind="primitive", operator="vox1.dt", args=()),
        "c": NodeSpec(kind="primitive", operator="vox1.and", args=("a", "b")),
    })
    graph.register("a")
    graph.register("b")
    graph.register("c")
    assert graph.on_complete("a") == [], "one dependency still unmet"
    assert graph.on_complete("b") == ["c"]
