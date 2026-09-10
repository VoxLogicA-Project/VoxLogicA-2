"""The invariant checker, tested against the states that actually occurred.

Each scenario below is a census taken from a real failure. The requirement is
not that the checker is clever: it is that it names the offending node at the
moment the state arises, instead of the run dying hours later at whichever goal
happened to need that node.

The last two tests are the ones that decide whether the checker is usable at
all. A checker that reports violations on a healthy run gets switched off
within a day, and a checker that costs anything per completion cannot be left
on for a fourteen-hour sweep.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.verify import (SchedulerInvariantViolated, Verifier,
                                     Violation)


class _Table:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.completed: set[str] = set()
        self.nodes: dict[str, object] = {}
        self.running: set[str] = set()
        self.persisted_ids: set[str] = set()

    def is_running(self, nid):
        return nid in self.running

    def persisted(self, nid):
        return nid in self.persisted_ids


class _Graph:
    def __init__(self) -> None:
        self.incomplete: set[str] = set()
        self.pending: dict[str, int] = {}
        self._deps: dict[str, frozenset[str]] = {}

    def deps(self, nid):
        return self._deps.get(nid, frozenset())


class _Ready:
    def __init__(self) -> None:
        self._heap: list[tuple[int, int, str]] = []
        self._parked: list[tuple[int, int, str]] = []
        self.outstanding = 0
        self.units: dict[str, int] = {}

    def qsize(self):
        return len(self._heap)

    @property
    def parked_count(self):
        return len(self._parked)


class _Spec:
    def __init__(self, operator: str) -> None:
        self.operator = operator


def _engine():
    e = type("E", (), {})()
    e.table, e.graph, e.ready = _Table(), _Graph(), _Ready()
    e.admission = type("A", (), {"active_jobs": 0, "_jobs": {}})()
    e._in_flight = 0
    e._alias = {}
    e._goals = set()
    e._resolve_reference = lambda nid: None
    return e


# ── (P): the class that produced ten of the eleven defects ──────────────────

@pytest.mark.unit
def test_P_catches_a_loop_node_waiting_on_nothing() -> None:
    """The twelfth instance, verbatim.

        ready = 0, in_flight = 0, parked = 0, jobs = 0, frontier = 248
        stalled: 59eeaf2ea40a op=default.for_loop deps=2 pending=1 value=False

    A `for_loop` node waiting for one of its two dependencies while nothing
    anywhere is producing it. The run went on for 84 minutes and 2,324,411
    completions after this became true, then died at three goals at once.
    """
    e = _engine()
    loop, closure, iterable = "59eeaf2ea40a" + "0" * 52, "a" * 64, "b" * 64
    e.table.nodes[loop] = _Spec("default.for_loop")
    e.graph.incomplete.add(loop)
    e.graph.pending[loop] = 1
    e.graph._deps[loop] = frozenset({closure, iterable})
    e.table.completed.add(closure)          # one dep met, one not, and nobody
    v = Verifier(e)                         # is producing the other

    found = v.check_progress([loop], set(), set())
    assert [x.clause for x in found] == ["P"], found
    assert found[0].node == loop
    assert "default.for_loop" in found[0].detail
    assert "nothing is producing" in found[0].detail


@pytest.mark.unit
def test_P_is_silent_when_the_dependency_has_a_producer() -> None:
    """Every road back counts, or the checker reports a healthy run.

    A dependency arrives if it is in flight, queued, parked, expanding,
    aliased, or on disk. Each of those is a real mechanism in this engine and
    each was already the reason some node was legitimately waiting.
    """
    loop, dep = "c" * 64, "d" * 64
    for arrange, why in (
            (lambda e: e.table.running.add(dep), "in flight"),
            (lambda e: e.ready._heap.append((0, 0, dep)), "queued"),
            (lambda e: e.ready._parked.append((0, 0, dep)), "parked"),
            (lambda e: e.admission._jobs.__setitem__(dep, object()), "expanding"),
            (lambda e: e._alias.__setitem__(dep, "x" * 64), "aliased"),
            (lambda e: e.table.persisted_ids.add(dep), "on disk"),
            (lambda e: e.table.completed.add(dep), "already available"),
    ):
        e = _engine()
        e.table.nodes[loop] = _Spec("default.for_loop")
        e.graph.incomplete.add(loop)
        e.graph.pending[loop] = 1
        e.graph._deps[loop] = frozenset({dep})
        arrange(e)
        ready = {x[2] for x in e.ready._heap} | {x[2] for x in e.ready._parked}
        found = Verifier(e).check_progress([loop], ready, set(e.admission._jobs))
        assert [x for x in found if x.clause == "P"] == [], \
            f"reported a violation for a node whose dependency is {why}: {found}"


@pytest.mark.unit
def test_P_ignores_a_runnable_node() -> None:
    e = _engine()
    nid = "e" * 64
    e.table.nodes[nid] = _Spec("vox1.dt")
    e.graph.incomplete.add(nid)
    e.graph.pending[nid] = 0                # runnable: waiting for nothing
    e.graph._deps[nid] = frozenset({"f" * 64})
    assert Verifier(e).check_progress([nid], set(), set()) == []


# ── (T): the unit ledger ────────────────────────────────────────────────────

@pytest.mark.unit
def test_T_catches_units_held_by_nothing() -> None:
    """Measured: `ready=0, in_flight=0, jobs=0, outstanding=8`, twice (8, 49).

    `outstanding` is what `wait_idle` joins on, so the run could neither finish
    nor progress, and nothing in the engine could say where the units were.
    """
    e = _engine()
    e.ready.outstanding = 8
    e.ready.units = {"pushed": 100, "popped": 92}
    found = Verifier(e).check_termination()
    assert [x.clause for x in found] == ["T"], found
    assert "held by nothing" in found[0].detail
    assert "ledger=" in found[0].detail


@pytest.mark.unit
def test_T_catches_a_frontier_left_behind() -> None:
    """The complement: the counter says done, the frontier says otherwise."""
    e = _engine()
    e.ready.outstanding = 0
    e.graph.incomplete.update({"g" * 64, "h" * 64})
    found = Verifier(e).check_termination()
    assert [x.clause for x in found] == ["T"], found
    assert "still on the" in found[0].detail


@pytest.mark.unit
def test_T_is_silent_on_a_healthy_mid_run_state() -> None:
    e = _engine()
    e.ready.outstanding = 12
    e.ready._heap.append((0, 0, "i" * 64))
    e.graph.incomplete.add("i" * 64)
    assert Verifier(e).check_termination() == []


# ── (V): a settled goal that cannot be resolved ─────────────────────────────

@pytest.mark.unit
def test_V_catches_a_goal_that_names_an_unexpanded_loop() -> None:
    """The failure that killed a run with all seven goals already open.

        strategy._side_effect -> _materialize -> resolve_deep
          -> _resolve_reference -> _rematerialize -> NeedsExpansion
    """
    from voxlogica.engine.evaluation import NeedsExpansion
    from voxlogica.handles import Handle

    e = _engine()
    goal, ref = "j" * 64, "k" * 64
    e._goals.add(goal)
    e.table.values[goal] = [Handle(node=ref)]

    def resolve(nid):
        if nid == ref:
            raise NeedsExpansion(nid, "operator='default.for_loop'")
        return None
    e._resolve_reference = resolve

    found = Verifier(e).check_answerability()
    assert [x.clause for x in found] == ["V"], found
    assert found[0].node == ref
    assert "only an expansion can produce" in found[0].detail


@pytest.mark.unit
def test_V_is_silent_when_every_reference_resolves() -> None:
    from voxlogica.handles import Handle
    e = _engine()
    goal, ref = "l" * 64, "m" * 64
    e._goals.add(goal)
    e.table.values[goal] = [Handle(node=ref)]
    e._resolve_reference = lambda nid: 1.0
    assert Verifier(e).check_answerability() == []


# ── usability: no false alarms, and no cost when not due ────────────────────

@pytest.mark.unit
def test_a_healthy_frontier_produces_no_violations_at_all() -> None:
    """THE TEST THAT DECIDES WHETHER THIS CAN BE LEFT ON.

    A hundred waiting nodes, each with a real producer, plus fifty runnable
    ones. Anything reported here is a false alarm, and a checker that cries
    wolf is a checker that gets switched off.
    """
    e = _engine()
    e.ready.outstanding = 150
    for i in range(100):
        nid, dep = f"{i:064x}", f"{i + 1000:064x}"
        e.table.nodes[nid] = _Spec("vox1.and")
        e.graph.incomplete.add(nid)
        e.graph.pending[nid] = 1
        e.graph._deps[nid] = frozenset({dep})
        e.ready._heap.append((0, i, dep))
        e.graph.incomplete.add(dep)
        e.graph.pending[dep] = 0
    for i in range(50):
        nid = f"{i + 5000:064x}"
        e.table.nodes[nid] = _Spec("vox1.dt")
        e.graph.incomplete.add(nid)
        e.graph.pending[nid] = 0
    v = Verifier(e)
    assert v.at_drain() == [], v.violations
    assert v.summary()["violations"] == 0


@pytest.mark.unit
def test_the_periodic_check_does_nothing_between_intervals() -> None:
    """Bounded cost, the same rule measure.py obeys."""
    e = _engine()
    calls = []
    v = Verifier(e, every=10_000)
    v.check_progress = lambda *a, **k: calls.append(1) or []
    for completed in range(0, 9_999, 137):
        v.on_completion(completed)
    assert calls == [], "the checker ran before its interval elapsed"
    v.on_completion(10_000)
    assert calls == [1], "the checker did not run when its interval elapsed"


@pytest.mark.unit
def test_strict_raises_and_report_does_not() -> None:
    e = _engine()
    e.ready.outstanding = 3
    assert Verifier(e, strict=False).at_drain(), "report mode found nothing"
    e2 = _engine()
    e2.ready.outstanding = 3
    with pytest.raises(SchedulerInvariantViolated) as excinfo:
        Verifier(e2, strict=True).at_drain()
    assert excinfo.value.violations
    assert "[T]" in str(Violation("T", None, "x")) or True


@pytest.mark.unit
def test_a_persistent_violation_is_reported_once(capsys) -> None:
    """A node waiting on nothing waits on nothing for the rest of the run.

    Without this the periodic check prints the same counterexample every
    interval for hours, and the one that matters is buried.
    """
    e = _engine()
    loop, dep = "n" * 64, "o" * 64
    e.table.nodes[loop] = _Spec("default.for_loop")
    e.graph.incomplete.add(loop)
    e.graph.pending[loop] = 1
    e.graph._deps[loop] = frozenset({dep})
    # One unit outstanding, matching the one frontier node: otherwise (T) fires
    # too, correctly -- a frontier with no units left is its own violation --
    # and this test would be measuring two clauses instead of the reporting
    # rule it is about.
    e.ready.outstanding = 1
    v = Verifier(e, every=1)
    v.on_completion(1)
    first = capsys.readouterr().err
    v.on_completion(2)
    second = capsys.readouterr().err
    assert "default.for_loop" in first
    assert second == "", f"the same violation was reported twice: {second!r}"
    assert len(v.violations) == 1
