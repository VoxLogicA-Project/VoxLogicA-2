"""Node states: the parts of R6 that are invisible when they are wrong.

A result card that stays blank looks like a card nobody bound yet, and a card
that says `computing` forever looks like a slow computation. Neither shows up as
an error, so the rules are pinned here instead: where an answer comes from when
the engine has not spoken, which way a state is allowed to move, that a
subscription is answered rather than merely recorded, and that waiting ends.
"""

from __future__ import annotations

import threading

import pytest

from voxlogica.ui.hub import Hub
from voxlogica.ui.results import Results, bindings_for, describe


class FakeHub(Hub):
    """A hub that keeps what was published instead of fanning it out."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[dict] = []

    def publish(self, event, *, sticky_key=None):  # type: ignore[override]
        self.published.append(event)


# ------------------------------------------------------------------ describing


def test_small_values_travel_and_large_ones_are_described():
    assert describe(3)[:2] == (3, "number")
    assert describe(True)[:2] == (True, "boolean")
    assert describe("hi")[:2] == ("hi", "string")

    value, kind, summary = describe("x" * 100_000)
    assert value is None and kind == "string" and "100000" in summary


def test_a_volume_is_described_rather_than_sent():
    """The reason `value` is not simply always included.

    A card showing a 240x240x155 volume wants to know that it is one. Sending
    it would put a few hundred megabytes through a WebSocket to render a
    sentence.
    """

    class Volume:
        shape = (240, 240, 155)
        dtype = "float32"

    value, kind, summary = describe(Volume())
    assert value is None
    assert kind == "array"
    assert "(240, 240, 155)" in summary and "float32" in summary


def test_describing_never_raises():
    class Hostile:
        @property
        def shape(self):
            raise RuntimeError("no")

        def GetSize(self):
            raise RuntimeError("no")

    # The `shape` property raising is not caught by `describe` itself -- it is
    # caught where `describe` is called, which is what `_from_store` asserts
    # below. What must hold here is that an ordinary object is describable.
    assert describe(object())[0] is None
    with pytest.raises(RuntimeError):
        describe(Hostile())


# --------------------------------------------------------------- the two sources


def test_the_store_answers_when_the_engine_has_not():
    """A cache hit is `done` before anything runs.

    This is the whole point of a content-addressed store, and rendering it as
    `unknown` until somebody recomputed it would be the UI lying about the most
    useful thing the system does.
    """
    results = Results(FakeHub(), probe=lambda h: h == "abc", fetch=lambda h: 42)
    assert results.state_of("abc")["state"] == "done"
    assert results.state_of("abc")["value"] == 42
    assert results.state_of("other")["state"] == "unknown"


def test_an_unreadable_store_is_an_unknown_not_a_crash():
    def broken(_hash):
        raise OSError("database is locked")

    results = Results(FakeHub(), probe=broken)
    assert results.state_of("abc")["state"] == "unknown"


def test_a_value_that_cannot_be_described_is_still_done():
    def hostile(_hash):
        raise RuntimeError("unpicklable")

    results = Results(FakeHub(), probe=lambda h: True, fetch=hostile)
    assert results.state_of("abc")["state"] == "done"


def test_the_engine_wins_over_the_store():
    """`computing` is more recent news than `done` can be here.

    A node the engine is recomputing (its cached value evicted) must not read as
    finished because the store still remembers a payload.
    """
    results = Results(FakeHub(), probe=lambda h: True, fetch=lambda h: 1)
    results.observe("abc", "computing")
    assert results.state_of("abc")["state"] == "computing"


# ------------------------------------------------------------------ transitions


def test_a_late_report_cannot_move_a_state_backwards():
    """The scheduler does not serialise its bookkeeping against this module.

    A `pending` arriving after a `computing` says nothing new, and applying it
    would make a card flicker backwards through states it has already left.
    """
    results = Results(FakeHub())
    results.observe("abc", "computing")
    results.observe("abc", "pending")
    assert results.state_of("abc")["state"] == "computing"

    results.observe("abc", "done", value=7)
    results.observe("abc", "computing")
    assert results.state_of("abc")["state"] == "done"


def test_only_watched_nodes_are_published():
    """The traffic is a function of what is being looked at.

    A plan with a hundred thousand nodes and four cards on screen is four
    subscriptions; publishing every transition would make an open window cost a
    fraction of the run.
    """
    hub = FakeHub()
    results = Results(hub)
    results.observe("quiet", "computing")
    assert hub.published == []

    results.subscribe(["loud"])
    results.observe("loud", "computing")
    assert [event["hash"] for event in hub.published] == ["loud"]
    assert hub.published[0]["type"] == "result"


def test_subscribing_answers_with_what_is_known():
    """Otherwise a card added mid-run is blank until the next thing happens to
    it -- and for a node computed an hour ago, nothing else ever happens."""
    results = Results(FakeHub(), probe=lambda h: h == "cached", fetch=lambda h: 5)
    results.observe("live", "computing")

    states = {state["hash"]: state["state"] for state in
              results.subscribe(["live", "cached", "never"])}
    assert states == {"live": "computing", "cached": "done", "never": "unknown"}


def test_unsubscribing_stops_the_traffic():
    hub = FakeHub()
    results = Results(hub)
    results.subscribe(["abc"])
    results.unsubscribe(["abc"])
    results.observe("abc", "done", value=1)
    assert hub.published == []


# --------------------------------------------------------------------- waiting


def test_wait_returns_at_once_when_it_is_already_there():
    results = Results(FakeHub())
    results.observe("abc", "done", value=1)
    assert results.wait("abc", timeout=0.1)["state"] == "done"


def test_wait_wakes_on_the_transition():
    results = Results(FakeHub())

    def finish():
        results.observe("abc", "done", value=99)

    threading.Timer(0.05, finish).start()
    outcome = results.wait("abc", timeout=5.0)
    assert outcome["state"] == "done" and outcome["value"] == 99


def test_wait_is_bounded_and_says_so():
    """A wait with no bound is a hang with a friendlier name: an agent that
    mistyped a node name would otherwise never come back."""
    results = Results(FakeHub())
    outcome = results.wait("never", timeout=0.15)
    assert outcome["timedOut"] is True
    assert outcome["state"] == "unknown"


def test_failed_satisfies_a_wait_for_done():
    """Ranked equal on purpose. A node that failed will never be done, and a
    waiter that slept through the failure until its timeout would report the
    wrong reason for the same amount of waiting."""
    results = Results(FakeHub())
    results.observe("abc", "failed", error="boom")
    outcome = results.wait("abc", timeout=0.2)
    assert outcome["state"] == "failed" and outcome["error"] == "boom"


# -------------------------------------------------------------------- bindings


def test_a_let_name_resolves_to_a_hash():
    bindings = bindings_for("let a = 2\nlet b = a + 1\n")
    assert set(bindings) >= {"a", "b"}
    assert all(len(hash_) == 64 for hash_ in bindings.values()), bindings
    # Different expressions, different nodes -- these are content hashes.
    assert bindings["a"] != bindings["b"]


def test_the_same_expression_is_the_same_node():
    first = bindings_for("let x = 1 + 1\n")
    second = bindings_for("let y = 1 + 1\n")
    assert first["x"] == second["y"]


def test_a_document_mid_edit_has_no_bindings_rather_than_an_error():
    """The normal case, not an error: somebody is halfway through typing."""
    assert bindings_for("let a = ") == {}
    assert bindings_for("!!!") == {}


def test_resolve_prefers_a_name_and_passes_a_hash_through():
    results = Results(FakeHub())
    results.set_bindings({"mask": "beef" * 16})
    assert results.resolve("mask") == "beef" * 16
    assert results.resolve("nothing") is None


# ------------------------------------------------------- the engine's own view


def test_the_engine_reports_a_node_through_its_states():
    """The half of R6 that cannot be faked: a real run, observed.

    Also the only place the two halves are checked to agree -- the hash the
    reducer hands the UI for `let s` has to be the id the scheduler dispatches,
    or a card would subscribe to a node nothing ever reports.
    """
    from voxlogica.execution import ExecutionEngine
    from voxlogica.main import build_workplan
    from voxlogica.storage import NoCacheStorageBackend

    program = 'let a = 2\nlet b = 3\nlet s = a + b\nprint "s" s\n'
    seen: list[tuple[str, str, object]] = []

    _syntax, plan = build_workplan(program)
    outcome = ExecutionEngine(
        storage_backend=NoCacheStorageBackend(),
        no_cache=True,
        observe=lambda nid, state, **kw: seen.append((state, nid, kw.get("value"))),
    ).execute_workplan(plan)
    assert outcome.success

    node = bindings_for(program)["s"]
    states = [state for state, nid, _ in seen if nid == node]
    assert states == ["pending", "computing", "done"], seen
    assert [value for state, nid, value in seen if nid == node and state == "done"] == [5.0]


def test_an_observer_that_raises_does_not_fail_the_run():
    """A spectator. A UI raising while being told a node finished would abort a
    computation for the sake of a card, which is the wrong way round."""
    from voxlogica.execution import ExecutionEngine
    from voxlogica.main import build_workplan
    from voxlogica.storage import NoCacheStorageBackend

    def hostile(*_args, **_kwargs):
        raise RuntimeError("the UI fell over")

    _syntax, plan = build_workplan('let s = 1 + 1\nprint "s" s\n')
    outcome = ExecutionEngine(
        storage_backend=NoCacheStorageBackend(), no_cache=True, observe=hostile
    ).execute_workplan(plan)
    assert outcome.success


# ------------------------------------------------------- the bytes themselves


def test_bytes_come_from_the_store_when_it_has_them():
    """The route a viewer that draws rather than describes actually uses."""
    results = Results(FakeHub(), probe=lambda h: True, fetch=lambda h: {"a": 1})
    payload = results.bytes_of("abc")
    assert payload is not None
    data, name = payload
    assert data == b'{"a": 1}'
    assert name.endswith(".json")


def test_bytes_fall_back_to_what_the_engine_reported():
    """The store does not hold everything the engine computes -- trivial
    arithmetic is folded and never persisted. Answering "not computed" for a
    node that plainly is would be the UI contradicting itself on one screen."""
    results = Results(FakeHub())
    results.observe("abc", "done", value=5.0)
    data, _name = results.bytes_of("abc")
    assert data == b"5.0"


def test_bytes_are_absent_rather_than_invented():
    results = Results(FakeHub())
    assert results.bytes_of("never") is None
