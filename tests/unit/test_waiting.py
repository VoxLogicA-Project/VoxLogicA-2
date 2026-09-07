"""The wait/claim protocol's invariants.

Every test here is a failure that actually happened while changing how the
engine rebuilds an evicted value. They are written against the ledger alone,
with no engine, because that is the point of having it separate: these
properties can be true or false without running a program.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.waiting import WaitLedger


@pytest.fixture
def ledger() -> WaitLedger:
    return WaitLedger()


# ── invariant 1: a node waiting for several inputs fires once, at the end ────

@pytest.mark.unit
def test_two_waits_need_two_arrivals(ledger: WaitLedger) -> None:
    """The failure this protocol exists for.

    The old graph SET the wait count to 1 on each request, so a node asked to
    wait for two inputs waited for one: it fired on the first arrival, came
    back, found the second missing, asked again -- and if that had completed
    in between, the wakeup was gone. 2,950 nodes stuck on inputs that never
    arrived, only under load.
    """
    assert ledger.add_wait("n", "a")
    assert ledger.add_wait("n", "b")
    assert ledger.waiting_for("n") == 2

    assert ledger.arrived("a") == [], "one of two arrivals must not fire it"
    assert ledger.waiting_for("n") == 1
    assert ledger.arrived("b") == ["n"], "the last arrival fires it"
    assert not ledger.is_waiting("n")


@pytest.mark.unit
def test_firing_is_once_and_not_again(ledger: WaitLedger) -> None:
    ledger.add_wait("n", "a")
    assert ledger.arrived("a") == ["n"]
    assert ledger.arrived("a") == [], "a second announcement fires nobody"


@pytest.mark.unit
def test_many_waiters_on_one_input_all_fire(ledger: WaitLedger) -> None:
    for node in ("n1", "n2", "n3"):
        ledger.add_wait(node, "shared")
    assert sorted(ledger.arrived("shared")) == ["n1", "n2", "n3"]


@pytest.mark.unit
def test_order_of_arrivals_does_not_matter(ledger: WaitLedger) -> None:
    for dep in ("a", "b", "c"):
        ledger.add_wait("n", dep)
    assert ledger.arrived("c") == []
    assert ledger.arrived("a") == []
    assert ledger.arrived("b") == ["n"]


# ── invariant 2: no wakeup is ever lost ─────────────────────────────────────

@pytest.mark.unit
def test_waiting_for_something_already_here_is_refused(ledger: WaitLedger) -> None:
    """Told, not silently parked.

    A wait registered after the arrival would never be answered. The old code
    recorded it and the node waited for the rest of the run; the caller is now
    told so it can look again.
    """
    ledger.arrived("a")
    assert ledger.add_wait("n", "a") is False
    assert not ledger.is_waiting("n")


@pytest.mark.unit
def test_a_value_that_goes_away_can_be_waited_for_again(ledger: WaitLedger) -> None:
    """Eviction is normal, so the second wait must be legitimate."""
    ledger.arrived("a")
    assert ledger.add_wait("n", "a") is False
    ledger.gone("a")
    assert ledger.add_wait("n", "a") is True
    assert ledger.arrived("a") == ["n"]


@pytest.mark.unit
def test_arrival_of_something_nobody_wants_is_harmless(ledger: WaitLedger) -> None:
    assert ledger.arrived("orphan") == []


# ── the two orthogonal states ───────────────────────────────────────────────

@pytest.mark.unit
def test_computed_says_nothing_about_residency(ledger: WaitLedger) -> None:
    """Conflating these is why rebuilds had to happen outside the scheduler.

    A node whose value was evicted could not be scheduled again, because
    `completed` meant both "has run" and "its value is here".
    """
    ledger.mark_computed("n")
    ledger.arrived("n")
    ledger.gone("n")                     # evicted
    assert ledger.is_computed("n"), "it has still been computed"
    assert ledger.add_wait("w", "n"), "and may still be waited for"


# ── invariant 3: one claim count, honoured everywhere ───────────────────────

@pytest.mark.unit
def test_a_claimed_value_may_not_be_dropped(ledger: WaitLedger) -> None:
    assert ledger.may_drop("v")
    held = ledger.claim(["v"])
    assert not ledger.may_drop("v")
    ledger.unclaim(held)
    assert ledger.may_drop("v")


@pytest.mark.unit
def test_claims_nest(ledger: WaitLedger) -> None:
    """Two dispatches reading one value; the first to finish must not free it."""
    first = ledger.claim(["v"])
    second = ledger.claim(["v"])
    ledger.unclaim(first)
    assert not ledger.may_drop("v"), "the second reader still needs it"
    ledger.unclaim(second)
    assert ledger.may_drop("v")


@pytest.mark.unit
def test_claiming_several_and_releasing_them_together(ledger: WaitLedger) -> None:
    held = ledger.claim(["a", "b", "c"])
    assert all(not ledger.may_drop(v) for v in "abc")
    ledger.unclaim(held)
    assert all(ledger.may_drop(v) for v in "abc")


@pytest.mark.unit
def test_unclaiming_what_was_never_claimed_is_harmless(ledger: WaitLedger) -> None:
    ledger.unclaim(["never"])
    assert ledger.may_drop("never")
    assert ledger.claims_on("never") == 0


# ── what a hang should be able to report ────────────────────────────────────

@pytest.mark.unit
def test_stuck_reports_who_waits_and_for_how_many(ledger: WaitLedger) -> None:
    ledger.add_wait("n1", "a")
    ledger.add_wait("n2", "b")
    ledger.add_wait("n2", "c")
    ledger.arrived("a")
    assert ledger.stuck() == {"n2": 2}
