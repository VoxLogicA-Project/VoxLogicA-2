"""Deterministic guards for the memory-bounding fix (no hardware/timing deps).

The failure these protect against — a large run's RSS climbing past the budget
until the OS OOM-kills it silently — depends on a slow disk and slow kernels to
manifest as a live crash, which a fast dev machine cannot reproduce on demand.
These tests instead pin the mechanisms the fix installs, directly:

1. ``NodeTable.accounted_bytes`` folds the persist backlog into the resident
   total, so a value evicted from the live tier but still queued for writing is
   still counted (the old ``live_bytes`` under-reported it — the invisible-RSS
   gap).
2. ``LoopAdmission._has_room`` is DEMAND-DRIVEN: it admits a new loop body only
   when the ready queue would otherwise starve the workers (``qsize <
   workers``), never merely because bytes are under budget — that greedy rule
   is what let the engine open an entire window's worth of elements the
   instant memory allowed, independent of whether the workers could even use
   that concurrency. A hard ceiling remains a backstop: past it, admission
   refuses even when starving, unless the run is truly wedged (nothing
   running, nothing ready).
3. ``ComputationEngine._reclaim_memory`` bounds the *sequence-assembly floor*:
   a completed loop body's value would otherwise stay refcount-pinned in the
   live tier for the whole unroll (every consumer-holding node does, until its
   last consumer runs) — for a wide loop that means peak RSS tracks element
   count, not concurrency, and no admission policy can reclaim memory already
   committed to finished bodies. Once a value is durably persisted, dropping
   the RAM copy early and reloading it on demand is safe and is what this
   mechanism does under memory pressure.
"""

from __future__ import annotations

import types
from collections import deque

import pytest

from voxlogica.engine.admission import LoopAdmission, _Job
from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeSpec
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_accounted_bytes_counts_persist_backlog() -> None:
    """A value evicted from the live tier but still in the write backlog stays
    counted in accounted_bytes — the resident total admission must bound."""
    table = NodeTable(backend=None)
    # Attach a stand-in persister exposing only what accounted_bytes reads.
    table._persister = types.SimpleNamespace(pending_bytes=0)

    table.live_bytes = 500
    table._persister.pending_bytes = 0
    assert table.accounted_bytes == 500

    # A large value was handed to the writer and then evicted from the live
    # tier: live_bytes drops, but the object is still resident in the queue.
    table.live_bytes = 0
    table._persister.pending_bytes = 800
    assert table.accounted_bytes == 800, "backlog must count toward resident total"

    # With no persister at all (--no-cache), accounted == live (graceful).
    table._persister = None
    table.live_bytes = 123
    assert table.accounted_bytes == 123


def _admission_with(accounted: int, qsize: int, *, workers: int, hard: int,
                    idle: bool) -> tuple[LoopAdmission, _Job]:
    """A LoopAdmission whose _has_room inputs are all stubbed to fixed values."""
    adm = LoopAdmission.__new__(LoopAdmission)  # bypass __init__; set only what _has_room reads
    adm.window = 8
    adm.workers = workers
    adm.hard_live_bytes = hard
    adm.graph = types.SimpleNamespace(table=types.SimpleNamespace(accounted_bytes=accounted))
    adm.ready = types.SimpleNamespace(qsize=lambda: qsize)
    adm._idle = lambda: idle
    return adm, _Job(loop_id="loop", priority=0)


@pytest.mark.unit
def test_admission_is_demand_driven_not_budget_driven() -> None:
    """Being far under budget does NOT admit if the queue is already fed.

    This is the discriminating case for the fix: the old rule admitted
    whenever bytes were under budget, regardless of queue depth, which is
    exactly what let the engine open an entire window's worth of elements the
    instant memory allowed. The new rule only opens the next body when doing
    so is needed to keep the workers fed.
    """
    hard, workers = 1_000_000, 4

    # Deep under budget, but the queue already has >= workers ready items:
    # nothing to gain from admitting more — refuse.
    adm, job = _admission_with(10, qsize=workers, workers=workers, hard=hard, idle=False)
    assert adm._has_room(job) is False

    # Same tiny budget usage, but the queue is shallow (would starve workers):
    # admit.
    adm, job = _admission_with(10, qsize=0, workers=workers, hard=hard, idle=False)
    assert adm._has_room(job) is True


@pytest.mark.unit
def test_hard_ceiling_is_a_backstop_over_demand() -> None:
    """Past the hard ceiling, admission refuses even while starving — except
    to break a true wedge (nothing running, nothing ready)."""
    hard, workers = 1500, 4

    # Starving queue, but over the hard ceiling: refuse (memory drains first).
    adm, job = _admission_with(1600, qsize=0, workers=workers, hard=hard, idle=False)
    assert adm._has_room(job) is False

    # Over the ceiling but truly wedged: admit one unit so the run can never
    # deadlock.
    adm, job = _admission_with(1600, qsize=0, workers=workers, hard=hard, idle=True)
    assert adm._has_room(job) is True


@pytest.mark.unit
def test_window_is_an_absolute_cap() -> None:
    """No memory state lets a single loop exceed its window of in-flight bodies."""
    adm, job = _admission_with(0, qsize=0, workers=4, hard=1500, idle=True)
    job.in_flight = adm.window
    assert adm._has_room(job) is False, "window bounds concurrency regardless of free memory"


def _completed_node(table: NodeTable, nid: str, value: bytes) -> None:
    """Complete a fake node through the real persistence path, then drain the
    writer so ``table.persisted(nid)`` is durably true (as the reclaim sweep
    requires before it will ever evict)."""
    table.nodes[nid] = NodeSpec(kind="primitive", operator="test.blob")
    table.begin(nid)
    table.complete(nid, value, compute_ms=10.0, critical=False, persist=True)
    table.flush()


def _engine_stub(table: NodeTable, *, max_live_bytes: int) -> ComputationEngine:
    """A bare ComputationEngine exposing only what _reclaim_memory reads."""
    engine = ComputationEngine.__new__(ComputationEngine)  # bypass __init__
    engine.table = table
    engine.graph = types.SimpleNamespace(consumers={})
    engine.config = types.SimpleNamespace(max_live_bytes=max_live_bytes)
    engine._evict_candidates = deque()
    engine._evicted_early = 0
    return engine


@pytest.mark.unit
def test_reclaim_evicts_durably_persisted_pending_values_under_pressure(tmp_path) -> None:
    """The sequence-assembly-floor fix: a completed value still awaited by a
    future consumer (graph.consumers > 0) is evicted from the live tier once
    it is durably on disk and the resident total is over budget — the same
    situation a wide loop's unconsumed bodies are in for their whole unroll."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        _completed_node(table, "n1", b"x" * 1000)
        assert table.persisted("n1"), "must be durable before the fix may evict it"

        engine = _engine_stub(table, max_live_bytes=1)  # any resident bytes are "over budget"
        engine.graph.consumers["n1"] = 1  # a future consumer still needs this value
        engine._evict_candidates.append("n1")

        engine._reclaim_memory()

        assert "n1" not in table.values, "durably-persisted, still-pending value must be evicted"
        assert engine._evicted_early == 1
        # The consumer relationship itself is untouched — only the RAM copy is
        # dropped; a later _rematerialize/load call still finds it on disk.
        assert engine.graph.consumers["n1"] == 1
        assert table.load("n1") == b"x" * 1000
    finally:
        backend.close()


@pytest.mark.unit
def test_reclaim_is_a_noop_under_budget() -> None:
    """No pressure, no eviction — the mechanism must add zero overhead/risk
    when the run is comfortably under its memory budget."""
    table = NodeTable(backend=None)  # no persister: nothing could be reclaimed anyway
    table.values["n1"] = b"x" * 1000
    engine = _engine_stub(table, max_live_bytes=1_000_000_000)
    engine._evict_candidates.append("n1")

    engine._reclaim_memory()

    assert "n1" in table.values
    assert engine._evicted_early == 0


@pytest.mark.unit
def test_reclaim_skips_not_yet_persisted_candidates(tmp_path) -> None:
    """A value queued for writing but not yet durably confirmed must not be
    evicted (that would force a recompute, not a reload) — it is simply
    retried on a later sweep."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("n1")
        table.complete("n1", b"x" * 1000, compute_ms=10.0, critical=False, persist=True)
        # Deliberately do NOT flush: the write may still be in flight.

        engine = _engine_stub(table, max_live_bytes=1)
        engine.graph.consumers["n1"] = 1
        engine._evict_candidates.append("n1")

        engine._reclaim_memory()

        # Either it wasn't durable yet (untouched, requeued) or the writer won
        # the race and it's already durable (evicted) — both are correct;
        # what must never happen is losing the candidate outright.
        if "n1" in table.values:
            assert list(engine._evict_candidates) == ["n1"]
        else:
            assert engine._evicted_early == 1
    finally:
        table.flush()
        backend.close()
