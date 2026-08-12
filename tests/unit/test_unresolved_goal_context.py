"""An unresolved goal must leave enough behind to debug it.

A goal the scheduler finishes without a value is a scheduling bug, and those
are the ones that cannot be reproduced on demand: by the time the run ends the
frontier is empty and the state that would explain it is gone. It happened on a
369-case sweep -- 9 goals of 748 unresolved after three hours, engine drained
to in_flight=0/ready=0/parked=0 -- and the report named only the goal, which is
the victim rather than the cause. These pin what the report must carry.
"""

import json

import pytest

from voxlogica.engine.strategy import _unresolved_goal_context


class _Spec:
    def __init__(self, operator):
        self.operator = operator


class _Table:
    def __init__(self, completed, nodes):
        self.completed = completed
        self.nodes = nodes

    def has_value(self, nid):
        return False

    def persisted(self, nid):
        return False

    def resident_by_operator(self, top=6):
        return [("vox1.dt", 4096)]


class _Graph:
    def __init__(self, table, edges):
        self.table = table
        self._edges = edges
        self.protected = set()
        self.incomplete = {"goal"}
        self.pending = {}
        self.registered_total = 41755

    def deps(self, nid):
        return frozenset(self._edges.get(nid, ()))


class _Engine:
    def __init__(self, graph):
        self.graph = graph
        self.table = graph.table

    def _memory_snapshot(self):
        return {"in_flight": 0, "ready": 0, "parked": 0, "evicted_early": 526910,
                "spill_pending": 751127, "resident_by_op": [], "census": {}, "bandwidth": 0}


class _Goal:
    id = "goal"
    name = "g68_score"
    operation = "print"


@pytest.fixture
def engine():
    # goal <- mid <- leaf ; leaf is done, mid is the one that never ran.
    edges = {"goal": ("mid",), "mid": ("leaf",), "leaf": ()}
    nodes = {n: _Spec(f"vox1.{n}") for n in edges}
    return _Engine(_Graph(_Table({"leaf"}, nodes), edges))


def test_names_the_stalled_node_not_only_the_goal(engine):
    ctx = _unresolved_goal_context(engine, _Goal())
    # `mid` is the culprit: incomplete, but every dependency of it is complete.
    assert "mid" in ctx["stalled_nodes"]
    assert "vox1.mid" in ctx["stalled_nodes"]
    assert ctx["goal_id"] == "goal"


def test_carries_the_terminal_counters(engine):
    ctx = _unresolved_goal_context(engine, _Goal())
    snap = json.loads(ctx["engine_snapshot"])
    # Drained-not-wedged is only distinguishable from a deadlock by these.
    assert snap["in_flight"] == 0 and snap["ready"] == 0
    assert snap["evicted_early"] == 526910
    assert ctx["registered_total"] == "41755"
    assert ctx["completed_total"] == "1"


def test_survives_an_engine_that_raises_on_attribute_access():
    """The report must not be replaced by the failure of the reporter."""

    class Wrecked:
        def __getattr__(self, name):
            raise RuntimeError("engine is wrecked")

    ctx = _unresolved_goal_context(Wrecked(), _Goal())
    assert "unreadable" in ctx["engine_state"]
    assert ctx["goal_id"] == "goal"


def test_survives_a_graph_that_raises_midway(engine):
    """A partial dump beats no dump: one bad accessor must not lose the rest."""

    def boom(nid):
        raise RuntimeError("deps exploded")

    engine.graph.deps = boom
    ctx = _unresolved_goal_context(engine, _Goal())
    assert "unavailable" in ctx["stalled_nodes"]
    assert ctx["goal_id"] == "goal"            # everything else still captured
    assert json.loads(ctx["engine_snapshot"])["in_flight"] == 0


def test_cone_walk_is_bounded_on_a_wide_graph():
    """The walk must terminate on a large cone rather than hang the reporter."""
    edges = {"goal": tuple(f"n{i}" for i in range(5000))}
    for i in range(5000):
        edges[f"n{i}"] = ()
    nodes = {n: _Spec("vox1.x") for n in edges}
    engine = _Engine(_Graph(_Table(set(), nodes), edges))
    ctx = _unresolved_goal_context(engine, _Goal())
    assert int(ctx["unresolved_cone_size"]) > 1
    assert ctx["stalled_nodes"].count("|") <= 4   # capped at 5 reported nodes
