"""Clause (P) must not call a chain being worked on a violation.

Production is transitive; the test was not. A dependency two or three levels
down a chain that is being computed had no producer by the strict definition,
and stayed that way for longer than one check interval, so phase-one
confirmation re-confirmed it rather than filtering it.

Measured on the sixty-case sweep: **~460 confirmed violations per periodic
check, for hours, while the run was healthy** -- 32 printed ids across 32
printed lines, zero repeats, a population turning over completely instead of
accumulating.

The cost is not the log lines. Both real failures of 2026-09-11/12 -- an
unresolved goal at drain, and a hard deadlock -- were caught by clause (T) and
never by (P), while (P) was crying wolf several hundred times an hour.

At drain the engine is quiescent, so "on the frontier" stops being an excuse and
the strict test is the right one. That is where (P) keeps its teeth.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.verify import Verifier


class _Graph:
    def __init__(self, deps, pending, incomplete):
        self._deps, self.pending, self.incomplete = deps, pending, set(incomplete)
        self.protected = set()

    def deps(self, nid):
        return frozenset(self._deps.get(nid, ()))


class _Table:
    def __init__(self, nodes):
        self.values, self.completed, self.nodes = {}, set(), nodes

    def persisted(self, nid):
        return False

    def is_running(self, nid):
        return False


class _Engine:
    def __init__(self, graph, table):
        self.graph, self.table = graph, table
        self._alias = {}
        self._in_flight = 0


def _chain():
    """a <- b <- c: every node registered, none of them running yet."""
    deps = {"a": ("b",), "b": ("c",), "c": ()}
    graph = _Graph(deps, {"a": 1, "b": 1, "c": 1}, {"a", "b", "c"})
    return _Engine(graph, _Table({"a": None, "b": None, "c": None}))


@pytest.mark.unit
def test_a_chain_being_worked_on_is_not_a_violation() -> None:
    """While the engine runs, a registered dependency IS a producer."""
    engine = _chain()
    verifier = Verifier(engine)
    found = verifier.check_progress(["a"], set(), set(), transitive=True)
    assert found == [], "`b` is registered; `a` waiting for it is the engine working"


@pytest.mark.unit
def test_at_drain_the_frontier_is_no_longer_an_excuse() -> None:
    """Quiescent: nothing is in flight, so a waiting chain IS stuck."""
    engine = _chain()
    verifier = Verifier(engine)
    found = verifier.check_progress(["a"], set(), set(), transitive=False)
    assert len(found) == 1, "with nothing running, nothing is producing `b`"
    assert found[0].clause == "P"


@pytest.mark.unit
def test_an_unregistered_dependency_is_a_violation_even_while_running() -> None:
    """The property (P) exists for: waiting on something nobody has registered."""
    deps = {"a": ("ghost",)}
    graph = _Graph(deps, {"a": 1}, {"a"})          # `ghost` is NOT on the frontier
    engine = _Engine(graph, _Table({"a": None}))
    found = Verifier(engine).check_progress(["a"], set(), set(), transitive=True)
    assert len(found) == 1, "nothing anywhere will ever produce `ghost`"
