"""Asking for a value: the demand set, and the one runner that satisfies it.

The properties here are the ones that only show up under a second click. A
single Run works under any implementation; what distinguishes this one is what
happens when somebody presses Run twice, presses it during a pass, asks for the
same node from two cards, or asks for something the document no longer contains.
"""

from __future__ import annotations

import threading

import pytest

from voxlogica.ui.compute import Compute
from voxlogica.ui.hub import Hub
from voxlogica.ui.results import Results

PROGRAM = 'let a = 2\nlet b = 3\nlet s = a + b\nprint "s" s\n'


class Recorder:
    """An execute that records what it was asked for, and can be held open."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail = False

    def __call__(self, source, nodes, observe):
        self.calls.append(list(nodes))
        self.entered.set()
        self.release.wait(5)
        if self.fail:
            raise RuntimeError("the plan would not compile")
        for node in nodes:
            observe(node, "done", value=1)


@pytest.fixture()
def wired():
    hub = Hub()
    results = Results(hub)
    recorder = Recorder()
    return Compute(hub, results, execute=recorder), results, recorder


def test_a_demand_is_queued_before_any_engine_has_an_opinion(wired):
    """A Run that showed nothing until the scheduler got to it would read as a
    button that did not work."""
    compute, results, recorder = wired
    recorder.release.clear()
    compute.demand(PROGRAM, ["aaa", "bbb"])
    assert results.state_of("aaa")["state"] == "pending"
    recorder.release.set()
    assert compute.wait_idle(5)


def test_the_same_node_asked_for_twice_is_asked_for_once(wired):
    """Hashes deduplicate, so five overlapping cards are one demand set."""
    compute, _results, recorder = wired
    recorder.release.clear()
    compute.demand(PROGRAM, ["aaa"])
    compute.demand(PROGRAM, ["aaa", "aaa"])
    recorder.release.set()
    assert compute.wait_idle(5)
    assert [sorted(set(call)) for call in recorder.calls] == [["aaa"]]


def test_a_demand_during_a_pass_joins_the_next_one(wired):
    """Never two engines: the second Run waits, and then finds the first pass's
    work already in the store, which is what content addressing is for."""
    compute, _results, recorder = wired
    recorder.release.clear()
    compute.demand(PROGRAM, ["first"])
    assert recorder.entered.wait(5)

    compute.demand(PROGRAM, ["second"])
    assert len(recorder.calls) == 1, "the second demand started a second engine"

    recorder.release.set()
    assert compute.wait_idle(5)
    assert recorder.calls == [["first"], ["second"]]


def test_a_failed_pass_leaves_nothing_claiming_to_be_queued(wired):
    """Otherwise a card sits at `pending` forever, which looks exactly like a
    computation that is merely slow."""
    compute, results, recorder = wired
    recorder.fail = True
    compute.demand(PROGRAM, ["doomed"])
    assert compute.wait_idle(5)
    assert results.state_of("doomed")["state"] == "failed"
    assert "would not compile" in results.state_of("doomed")["error"]


def test_a_failed_pass_does_not_stop_the_next_one(wired):
    """Somebody fixed the program; the runner has to still be alive to notice."""
    compute, results, recorder = wired
    recorder.fail = True
    compute.demand(PROGRAM, ["doomed"])
    assert compute.wait_idle(5)

    recorder.fail = False
    compute.demand(PROGRAM, ["fine"])
    assert compute.wait_idle(5)
    assert results.state_of("fine")["state"] == "done"


def test_a_demand_the_engine_never_reports_does_not_stay_queued(wired):
    """The most expensive kind of wrong this can be.

    Not every demanded node produces an event: one already satisfied, folded to
    a constant, or elided inside a fused cone is never dispatched. The
    optimistic `pending` written when it was asked for must not outlive the pass
    -- a card stuck there looks exactly like a computation that is merely slow.
    """
    compute, results, recorder = wired
    recorder.__call__ = lambda *_a, **_k: None  # reports nothing at all

    class Silent:
        def __call__(self, source, nodes, observe):
            return None

    compute._execute = Silent()
    compute.demand(PROGRAM, ["quiet"])
    assert compute.wait_idle(5)
    assert results.state_of("quiet")["state"] == "unknown"


def test_a_demand_already_in_the_store_ends_as_done(wired):
    """And when the store does know it, that is the answer -- which is the whole
    reason the optimism is dropped rather than overwritten with `unknown`."""
    hub = Hub()
    results = Results(hub, probe=lambda h: h == "cached", fetch=lambda h: 5)

    class Silent:
        def __call__(self, source, nodes, observe):
            return None

    compute = Compute(hub, results, execute=Silent())
    compute.demand(PROGRAM, ["cached"])
    assert compute.wait_idle(5)
    assert results.state_of("cached")["state"] == "done"


def test_a_pass_is_published_so_a_late_client_learns_of_it(wired):
    """Sticky, because a browser connecting mid-pass should not see a quiet UI
    that is in fact busy."""
    seen: list[dict] = []
    hub = Hub()
    hub.publish = lambda event, sticky_key=None: seen.append(event)  # type: ignore[method-assign]
    recorder = Recorder()
    compute = Compute(hub, Results(Hub()), execute=recorder)

    compute.demand(PROGRAM, ["x"])
    assert compute.wait_idle(5)
    states = [event["pass"]["state"] for event in seen if event.get("pass")]
    assert "running" in states and "done" in states


def test_nothing_asked_for_is_nothing_run(wired):
    compute, _results, recorder = wired
    compute.demand(PROGRAM, [])
    compute.demand(PROGRAM, [None, ""])  # type: ignore[list-item]
    assert recorder.calls == []


# ------------------------------------------------------- against a real engine
#
# Synchronously, through `_execute_with_engine` rather than through the runner
# thread. The threading properties are covered above with an injected execute;
# what is left to check is the part that talks to the engine, and running the
# engine on a background thread *inside pytest* wedges for reasons that have
# nothing to do with this module (it is fine in a plain interpreter -- the same
# demand through `Compute.demand` completes there). Testing it on this thread
# tests the code that matters without inheriting that.


def test_a_demand_computes_a_node_that_no_goal_asked_for():
    """The point of the whole module.

    `let a` is used by `s`, but nothing in the program prints or saves `a`
    itself. Under the CLI it is computed only as a dependency and no goal names
    it; a card on it must still be able to ask. That is what the injected
    `value` goal is for.
    """
    from voxlogica.ui.compute import _execute_with_engine
    from voxlogica.ui.results import bindings_for

    results = Results(Hub())
    node = bindings_for(PROGRAM)["a"]
    _execute_with_engine(PROGRAM, [node], results.observe)

    state = results.state_of(node)
    assert state["state"] == "done", state
    assert state["value"] == 2


def test_a_node_the_document_no_longer_contains_is_not_a_failure():
    """A card asked against a document that has since changed. Failing the pass
    would take every other card's demand down with it."""
    from voxlogica.ui.compute import _execute_with_engine

    results = Results(Hub())
    _execute_with_engine(PROGRAM, ["0" * 64], results.observe)  # must not raise


def test_a_demand_does_not_fire_the_document_effects():
    """Run on one card must not write another card's `save` to disk.

    A `value` goal materialises and does nothing else; asking for `a` therefore
    computes `a` and leaves the program's own goals alone.
    """
    from voxlogica.ui.compute import _execute_with_engine
    from voxlogica.ui.results import bindings_for

    results = Results(Hub())
    bindings = bindings_for(PROGRAM)
    _execute_with_engine(PROGRAM, [bindings["a"]], results.observe)

    # `s` is what the program prints. Asking only for `a` must not have run it.
    assert results.state_of(bindings["s"])["state"] == "unknown"
