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

import pathlib
import shutil
import types
from collections import deque

import pytest

from voxlogica.engine.admission import LoopAdmission, _Job
from voxlogica.engine.core import _EVICT_SWEEP, ComputationEngine
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


@pytest.mark.unit
def test_live_bytes_counts_shared_forwarded_value_once() -> None:
    table = NodeTable(backend=None)
    shared = bytearray(1024)

    table.set_value("spliced-sequence", shared)
    table.set_value("dynamic-loop", shared)
    assert table.live_bytes == 1024

    table.evict("spliced-sequence")
    assert table.live_bytes == 1024
    table.evict("dynamic-loop")
    assert table.live_bytes == 0


def _admission_with(accounted: int, qsize: int, *, workers: int, hard: int,
                    idle: bool) -> tuple[LoopAdmission, _Job]:
    """A LoopAdmission whose _has_room inputs are all stubbed to fixed values."""
    adm = LoopAdmission.__new__(LoopAdmission)  # bypass __init__; set only what _has_room reads
    adm.window = 8
    adm.workers = workers
    adm.hard_live_bytes = hard
    adm.soft_live_bytes = hard  # tests that care about the soft stop set it explicitly
    adm.graph = types.SimpleNamespace(
        table=types.SimpleNamespace(accounted_bytes=accounted, persist_over_budget=False))
    adm.ready = types.SimpleNamespace(qsize=lambda: qsize)
    adm._idle = lambda: idle
    adm._reclaim = lambda: None
    adm._escape_spent = False
    adm.ceiling_escapes = 0
    adm._window_now = adm.window
    adm.min_window_seen = adm.window
    adm._body_owner = {}   # on_complete: no owning job for the stubbed node
    adm._jobs = {}         # wake_jobs: nothing paused to nudge
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
    engine.config = types.SimpleNamespace(max_live_bytes=max_live_bytes,
                                          persist_min_compute_ms=1.0)
    engine.expander = types.SimpleNamespace(can_expand=lambda node: False)
    engine._evict_candidates = deque()
    engine._spill_pending = deque()
    engine._evicted_early = 0
    engine._spilled_early = 0
    engine._dispatch_pins = {}
    engine._goals = set()
    engine._recomputes = 0
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
def test_reclaim_never_evicts_a_value_a_dispatch_is_actively_reading(tmp_path) -> None:
    """Regression for the fusion-branch KeyError: a pool thread mid-dispatch
    reads `table.values[dep_id]` on its own thread, concurrently with any other
    worker's turn calling `_reclaim_memory` on the event loop. The previous
    eviction check only asked "does this still have an unmet consumer, and is
    it durable?" — both true for a dep that was JUST rematerialized for a
    dispatch that hasn't read it yet, so it could be evicted again before that
    read happens (KeyError in executor.py). `_dispatch_pins` closes exactly
    this window: while any dispatch holds a pin on a node, `_reclaim_memory`
    must skip it even if it is otherwise a textbook eviction candidate."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        _completed_node(table, "n1", b"x" * 1000)
        assert table.persisted("n1")

        engine = _engine_stub(table, max_live_bytes=1)
        engine.graph.consumers["n1"] = 1
        engine._evict_candidates.append("n1")
        engine._dispatch_pins["n1"] = 1  # simulates: a dispatch just rematerialized "n1"

        engine._reclaim_memory()

        assert "n1" in table.values, "a value pinned for an in-flight dispatch must survive reclaim"
        assert engine._evicted_early == 0
        # Once the (simulated) dispatch returns and unpins it, reclaim may
        # evict it again — pinning is a transient hold, not a permanent one.
        engine._unpin_dispatch(["n1"])
        engine._evict_candidates.append("n1")
        engine._reclaim_memory()
        assert "n1" not in table.values
        assert engine._evicted_early == 1
    finally:
        backend.close()


@pytest.mark.unit
def test_complete_reports_whether_value_will_be_durable(tmp_path) -> None:
    """complete() returns True iff the value was handed to the writer.

    This is the *worth-it* verdict for ordinary caching. It is deliberately no
    longer the gate on eviction candidacy: a value it declines still becomes
    durable on demand under pressure (see NodeTable.spill and
    test_reclaim_spills_a_never_persisted_value_then_evicts_it)."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        table.nodes["a"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("a")
        assert table.complete("a", b"v", persist=True) is True
        table.nodes["b"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("b")
        assert table.complete("b", b"v", persist=False) is False, "not worth persisting: not durable"
    finally:
        table.flush()
        backend.close()
    no_disk = NodeTable(backend=None)
    no_disk.nodes["c"] = NodeSpec(kind="primitive", operator="test.blob")
    no_disk.begin("c")
    assert no_disk.complete("c", b"v", persist=True) is False, "--no-cache: nothing is durable"


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
def test_evict_candidate_queue_is_lossless_past_former_cap() -> None:
    """A wide symbolic sweep may retain more than 200k pending values.

    Dropping the oldest candidate bounds a tiny deque by making its much larger
    image value permanently unreclaimable, defeating the hard memory ceiling.
    """
    table = NodeTable(backend=None)
    engine = _engine_stub(table, max_live_bytes=1)

    former_cap = 200_000
    for index in range(former_cap + 1):
        engine._track_evict_candidate(f"n{index}")

    assert len(engine._evict_candidates) == former_cap + 1
    assert engine._evict_candidates[0] == "n0"


@pytest.mark.unit
def test_reclaim_defers_an_expensive_in_flight_write_to_the_spill_queue(tmp_path) -> None:
    """An expensive value queued for writing but not yet durably confirmed must
    not be evicted (that would force a recompute, not a reload) — it moves to
    the writer-side queue so PASS 1 collects it the moment its write lands."""
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

        # Either it wasn't durable yet (moved to the spill queue, where spill()
        # reports its in-flight write) or the writer won the race and it's
        # already durable (evicted) — both are correct; what must never happen
        # is losing the candidate outright.
        if "n1" in table.values:
            assert list(engine._spill_pending) == ["n1"], "handed to PASS 1 for collection"
            table.flush()
            engine._reclaim_memory()  # write has landed: PASS 1 frees it now
            assert "n1" not in table.values
        assert engine._evicted_early == 1
    finally:
        table.flush()
        backend.close()


@pytest.mark.unit
def test_ceiling_escape_is_one_shot_until_a_completion() -> None:
    """The at-the-ceiling wedge-breaker must not be an unbounded escape.

    "Nothing running, nothing ready" is not a one-off state: it recurs after
    every completion, so an unconditional escape admits body after body while
    already past the hard ceiling. Measured on a one-case brats021 sweep, that
    walked the run from the 37.6 GB ceiling to a 56.7 GB OOM kill. Exactly one
    body may be admitted per completion.
    """
    hard, workers = 1500, 4
    adm, job = _admission_with(1600, qsize=0, workers=workers, hard=hard, idle=True)

    assert adm._has_room(job) is True, "a truly wedged run must still make progress"
    assert adm._has_room(job) is False, "the escape is spent until something completes"
    assert adm.ceiling_escapes == 1

    LoopAdmission.on_complete(adm, "some-node")  # a completion re-arms it
    assert adm._has_room(job) is True
    assert adm.ceiling_escapes == 2


@pytest.mark.unit
def test_reclaim_runs_before_the_ceiling_escape_is_considered() -> None:
    """At the ceiling the engine must first try to FREE memory. Reclaim can now
    make any resident value durable on demand, so the ceiling is normally a
    solvable state rather than a wedge — the escape is the last resort, not the
    first response."""
    hard, workers = 1500, 4
    adm, job = _admission_with(1600, qsize=0, workers=workers, hard=hard, idle=True)
    calls = []

    def reclaim() -> None:
        calls.append(1)
        adm.graph.table.accounted_bytes = 100  # the spill/evict sweep freed memory

    adm._reclaim = reclaim

    assert adm._has_room(job) is True
    assert calls == [1], "reclaim must be attempted before crossing the ceiling"
    assert adm.ceiling_escapes == 0, "memory was freed: this is ordinary admission, not an escape"


@pytest.mark.unit
def test_spill_makes_a_never_persisted_value_durable(tmp_path) -> None:
    """The valve the OOM exposed: persistence is worth-it gated by compute
    time, but a sub-millisecond kernel over a 35 MB mask is precisely the value
    that must be able to leave RAM. spill() bypasses that gate."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        table.nodes["cheap"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("cheap")
        # persist=False is what the worth-it gate does to a cheap kernel.
        assert table.complete("cheap", b"x" * 1000, compute_ms=0.0, persist=False) is False
        assert not table.persisted("cheap")

        assert table.spill("cheap") is True
        table.flush()
        assert table.persisted("cheap"), "spill must make the value reloadable"

        # Idempotent: a second spill does not re-submit an already durable value.
        assert table.spill("cheap") is True
    finally:
        backend.close()

    no_disk = NodeTable(backend=None)
    no_disk.values["x"] = b"v"
    assert no_disk.spill("x") is False, "--no-cache: nothing can be spilled"


@pytest.mark.unit
def test_reclaim_evicts_a_cheap_value_for_recompute_not_for_a_write(tmp_path) -> None:
    """A cheap undurable value under pressure is DROPPED, never written.

    The disk tier is a pure optimisation in this engine ("a miss falls back to
    recompute", manuscripts/parallel-engine): memory is bounded by eager
    eviction, admission control and bounded loop unrolling. Spending writes to
    buy back RAM spends the resource the workload is actually short of — this
    host's measured STREAM ceiling is 69.5 GB/s and the paper establishes the
    workload is bandwidth-bound at its optimum — on gzip and I/O for values
    nothing has asked for. A pressure spill wrote 300 GB in ~40 minutes.

    But "no write" must not mean "no exit": merely requeueing the candidate —
    "admission throttles" — re-created the day-one hole, because admission only
    gates NEW work and cannot reclaim bytes already committed to finished
    values. The assembly tail's cheap masks were then structurally
    unreclaimable: the eval-30 tail held 36.6 GB against a 25 GB budget, and
    the 369-case run grew past 42 GB until killed. The worth-it gate already
    judged this value cheaper to rebuild than to store, so the correct exit is
    eviction with recompute-on-demand (`_rematerialize`) — free of bandwidth,
    bounded in cost by the gate itself.
    """
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    submitted: list[str] = []
    try:
        table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("n1")
        table.complete("n1", b"x" * 1000, compute_ms=0.0, persist=False)  # cheap: not durable
        assert not table.persisted("n1")

        real_submit = table._persister.submit
        table._persister.submit = lambda nid, *a, **k: submitted.append(nid)

        engine = _engine_stub(table, max_live_bytes=1)
        engine.graph.consumers["n1"] = 1
        engine._evict_candidates.append("n1")

        engine._reclaim_memory()

        assert submitted == [], "reclaim must not queue writes to free memory"
        assert "n1" not in table.values, "cheap value must leave RAM; a miss recomputes"
        assert engine._evicted_early == 1
        # The consumer relationship survives — _rematerialize rebuilds the
        # value when that consumer finally dispatches.
        assert engine.graph.consumers["n1"] == 1
    finally:
        table._persister.submit = real_submit
        backend.close()


@pytest.mark.unit
def test_assembly_floor_of_cheap_values_is_bounded_under_pressure() -> None:
    """The tail-overshoot regression: a wide aggregation's completed-but-cheap
    inputs (all below the worth-it gate, all with a pending consumer) must not
    accumulate as an unreclaimable floor. Under pressure the sweep drains them
    even with no disk tier at all — recompute is the reload path."""
    table = NodeTable(backend=None)  # --no-cache: nothing can ever be durable
    engine = _engine_stub(table, max_live_bytes=1)
    n = 50
    for index in range(n):
        nid = f"body{index}"
        table.nodes[nid] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin(nid)
        # Distinct payloads: a folded constant would be one shared object and
        # the per-object accounting would (correctly) count it once.
        table.complete(nid, index.to_bytes(2, "big") * 500, compute_ms=0.0, persist=False)
        engine.graph.consumers[nid] = 1  # the aggregator still needs each one
        engine._evict_candidates.append(nid)
    assert table.live_bytes >= n * 1000

    engine._reclaim_memory()

    assert table.live_bytes == 0, "the whole cheap floor must drain under pressure"
    assert engine._evicted_early == n


@pytest.mark.unit
def test_recompute_scaffolding_disposal_is_pressure_gated() -> None:
    """Scaffolding is kept while there is room and dropped at the point of
    creation once there is not.

    Both halves were measured by getting them wrong. Always evicting rebuilt
    values a sibling recompute wanted moments later (23% recomputes vs a 1.4%
    baseline). Always tracking could not bound the tail: one `_rematerialize`
    recursion materializes a whole subtree inside ONE worker turn, while the
    256-per-sweep reclaim only runs BETWEEN turns — the census read
    ownerless=32.9G of a 38.0 GB peak with every other bucket bounded.
    """
    for over_budget, expect_resident in ((False, True), (True, False)):
        table = NodeTable(backend=None)
        budget = 1 if over_budget else 1_000_000_000
        engine = _engine_stub(table, max_live_bytes=budget)
        engine._alias = {}
        engine.executor = types.SimpleNamespace(
            _compute=lambda tbl, nid: b"rebuilt")
        # "scaf" is a dep of "want" whose own consumers have all run.
        table.nodes["want"] = NodeSpec(kind="primitive", operator="test.blob")
        table.nodes["scaf"] = NodeSpec(kind="primitive", operator="test.blob")
        engine.graph.deps = lambda nid: ["scaf"] if nid == "want" else []
        engine.graph.consumers["want"] = 1
        engine.graph.consumers["scaf"] = 0   # ownerless: pure scaffolding

        engine._rematerialize("want")

        assert ("scaf" in table.values) is expect_resident, (
            "under budget scaffolding is cached; over budget it must be dropped "
            "where it is created, not queued")
        assert "want" in table.values, "the value actually wanted must survive"


@pytest.mark.unit
def test_expansion_nodes_are_never_evicted_for_recompute() -> None:
    """A loop/sequence value is produced by the expander, not its kernel —
    `executor._compute` on a `for_loop` node raises (its closure argument
    rematerializes to None by design). Without a durable copy such a value
    must survive the sweep, however cheap it looks."""
    table = NodeTable(backend=None)
    engine = _engine_stub(table, max_live_bytes=1)
    engine.expander = types.SimpleNamespace(
        can_expand=lambda node: node.operator == "default.for_loop")
    table.nodes["loop"] = NodeSpec(kind="primitive", operator="default.for_loop")
    table.begin("loop")
    table.complete("loop", [b"body0"], compute_ms=0.0, persist=False)
    engine.graph.consumers["loop"] = 1
    engine._evict_candidates.append("loop")

    engine._reclaim_memory()

    assert "loop" in table.values, "expandable node without a durable copy must survive"
    assert engine._evicted_early == 0


@pytest.mark.unit
def test_resident_census_attributes_bytes_by_blocking_reason(tmp_path) -> None:
    """The memlog census must classify resident bytes correctly — it is the
    instrument every overshoot investigation reads first."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        engine = _engine_stub(table, max_live_bytes=1)

        _completed_node(table, "dur", b"d" * 100)       # durable -> evictable now
        engine.graph.consumers["dur"] = 1
        engine._evict_candidates.append("dur")

        table.nodes["cheap"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("cheap")
        table.complete("cheap", b"c" * 200, compute_ms=0.0, persist=False)
        engine.graph.consumers["cheap"] = 1             # undurable, tracked
        engine._evict_candidates.append("cheap")

        table.values["orphan"] = b"o" * 300             # ownerless AND untracked
        table._sizeof["orphan"] = 300

        table.values["goalv"] = b"g" * 400
        table._sizeof["goalv"] = 400
        engine._goals = {"goalv"}

        table.values["pinned"] = b"p" * 500
        table._sizeof["pinned"] = 500
        engine.graph.consumers["pinned"] = 1
        engine._dispatch_pins["pinned"] = 1
        engine._evict_candidates.append("pinned")

        census = engine._resident_census()

        assert census["durable"] == 100
        assert census["undurable"] == 200
        assert census["ownerless"] == 300
        assert census["goal"] == 400
        assert census["pinned"] == 500
        assert census["untracked"] == 300 and census["untracked_n"] == 1
    finally:
        table.flush()
        backend.close()


@pytest.mark.unit
def test_admission_waits_while_the_spill_valve_is_saturated() -> None:
    """Over the soft budget with the writer already behind, admission must stop.

    Spilling is the only way resident bytes leave RAM, and it refuses while the
    write backlog is over budget (NodeTable.spill) — so nothing the engine does
    can free memory until writes land. Measured when this stop was missing: the
    reclaim sweep queued 10.3 GB of unwritten values in ten seconds and the
    resident total crossed the hard ceiling from the spill itself. Waiting here
    is what turns an OOM into a slower run.
    """
    workers = 4
    adm, job = _admission_with(30_000, qsize=0, workers=workers, hard=100_000, idle=False)
    adm.soft_live_bytes = 25_000

    adm.graph.table.persist_over_budget = False
    assert adm._has_room(job) is True, "queue starving, valve healthy: admit"

    adm.graph.table.persist_over_budget = True
    assert adm._has_room(job) is False, "valve saturated over budget: wait for the writer"

    # Under the soft budget the writer's backlog is not admission's business.
    adm.graph.table.accounted_bytes = 10_000
    assert adm._has_room(job) is True


@pytest.mark.unit
def test_spill_respects_the_writer_backlog_budget(tmp_path) -> None:
    """spill() bypasses the worth-it gate but never the backlog budget: a queued
    write is not reclaimed memory, it is the same memory twice (live copy +
    queue reference) until the write lands."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("n1")
        table.complete("n1", b"x" * 1000, compute_ms=0.0, persist=False)

        table._persister._pending_bytes = table._persister._max_pending_bytes + 1
        assert table.spill("n1") is False, "saturated writer: refuse, let it drain"

        table._persister._pending_bytes = 0
        assert table.spill("n1") is True
    finally:
        table.flush()
        backend.close()


@pytest.mark.unit
def test_wedge_escape_is_returned_when_no_body_is_scheduled() -> None:
    """A warm body takes no window slot and schedules nothing, so no completion
    follows to re-arm the escape. Spending the token on it would wedge an idle
    run permanently."""
    adm, job = _admission_with(1600, qsize=0, workers=4, hard=1500, idle=True)
    adm._body_owner = {}
    adm.liveness = types.SimpleNamespace(staged=set())
    adm.graph.incomplete = set()
    adm._available = lambda body: True          # already materialized on disk
    adm._schedule = lambda body, prio: pytest.fail("a warm body must not be scheduled")
    job.staged.append("warm-body")

    LoopAdmission._admit(adm, job)

    assert adm._escape_spent is False, "an unspent escape must survive a warm body"


@pytest.mark.unit
def test_reloaded_value_becomes_a_reclaim_candidate_again(tmp_path) -> None:
    """Eviction CONSUMES a candidate; a reload must put it back.

    Without this, each evict/reload cycle retires one value from the valve's
    reach forever. Measured on the one-case sweep: the run ended holding 44 GB
    with an EMPTY candidate queue, its tail spent reloading values it could no
    longer release.
    """
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        _completed_node(table, "n1", b"x" * 1000)

        engine = _engine_stub(table, max_live_bytes=1)
        engine._goals = set()
        engine.graph.consumers["n1"] = 1
        engine._evict_candidates.append("n1")

        engine._reclaim_memory()
        assert "n1" not in table.values, "precondition: the candidate was consumed"
        assert not engine._evict_candidates

        # A later consumer needs it again: the reload path must re-register it.
        assert ComputationEngine._rematerialize(engine, "n1") == b"x" * 1000
        assert list(engine._evict_candidates) == ["n1"], "reload must re-arm reclaim"

        # And it is genuinely reclaimable a second time.
        engine._reclaim_memory()
        assert "n1" not in table.values
        assert engine._evicted_early == 2
    finally:
        backend.close()


@pytest.mark.unit
def test_goal_values_are_never_reclaim_candidates(tmp_path) -> None:
    """A goal's value is the run's output — it must stay resident even under
    pressure, on the completion path and on the reload path alike."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        _completed_node(table, "goal", b"x" * 1000)
        engine = _engine_stub(table, max_live_bytes=1)
        engine._goals = {"goal"}
        engine.graph.consumers["goal"] = 1

        engine._retrack_resident("goal")
        assert not engine._evict_candidates, "a goal must never be queued for reclaim"
    finally:
        backend.close()


@pytest.mark.unit
def test_spill_never_double_submits_an_in_flight_write(tmp_path) -> None:
    """Two writer threads on one payload can leave a truncated record behind —
    seen in a real run as `cannot reshape array of size 0` when the value was
    later reloaded. `complete` and `spill` therefore share ONE in-flight ledger:
    a value whose worth-it write is still in flight is already spillable, not a
    second write to schedule."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    submitted: list[str] = []
    try:
        table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("n1")
        table.complete("n1", b"x" * 1000, compute_ms=10.0, persist=True)  # write in flight
        assert "n1" in table._write_queued

        real_submit = table._persister.submit
        table._persister.submit = lambda nid, *a, **k: (submitted.append(nid),
                                                        real_submit(nid, *a, **k))[1]

        assert table.spill("n1") is True, "an in-flight write already makes it evictable"
        assert submitted == [], "must not queue the same payload twice"
    finally:
        table._persister.submit = real_submit
        table.flush()
        backend.close()


@pytest.mark.unit
def test_write_ledger_survives_an_evict_while_the_write_is_in_flight(tmp_path) -> None:
    """`release` can evict a value at any moment, including while its write is
    still queued. If the ledger entry went with it, a reload before the write
    landed would look like a fresh value and `spill` would queue the same
    payload a second time — the very race that truncates a record."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.blob")
        table.begin("n1")
        table.complete("n1", b"x" * 1000, compute_ms=10.0, persist=True)

        if not table.persisted("n1"):          # write still in flight
            table.evict("n1")
            assert "n1" in table._write_queued, "in-flight write must stay on the ledger"

        table.flush()                           # the write lands
        table.load("n1")
        table.evict("n1")
        assert "n1" not in table._write_queued, "landed write: ledger entry is redundant"
    finally:
        backend.close()


@pytest.mark.unit
def test_spilled_values_are_collected_ahead_of_the_unspilled_backlog(tmp_path) -> None:
    """The awaiting-durability queue must not be starved by the main backlog.

    With one shared FIFO, every sweep re-appended what it had just spilled, so a
    value whose write had landed sat tens of thousands of entries behind the
    fixed 256-per-sweep scan window. Measured: 110,495 spills against 1,972
    evictions — the engine paid to write and never collected the memory.
    """
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        _completed_node(table, "ready-to-free", b"x" * 1000)
        engine = _engine_stub(table, max_live_bytes=1)
        engine.graph.consumers["ready-to-free"] = 1
        engine._spill_pending.append("ready-to-free")
        # A backlog far larger than one sweep window sits on the OTHER queue.
        for index in range(_EVICT_SWEEP * 4):
            engine._evict_candidates.append(f"other{index}")

        engine._reclaim_memory()

        assert "ready-to-free" not in table.values, "a durable value must be freed this sweep"
        assert engine._evicted_early == 1
    finally:
        backend.close()


@pytest.mark.unit
def test_loop_window_shrinks_under_memory_pressure_and_recovers() -> None:
    """A fixed window assumes N bodies always fit; for image sweeps they do not.

    At window=16 the resident set of 16 open BraTS cases exceeded RAM and the
    engine fell back to spilling everything to disk — 300 node/s became 15. The
    window must therefore be an upper bound the engine walks down under pressure
    and back up when memory is comfortable, never a set point.
    """
    adm, job = _admission_with(0, qsize=0, workers=4, hard=100_000, idle=False)
    adm.window = 16
    adm._window_now = 16
    adm.min_window_seen = 16
    adm.soft_live_bytes = 10_000

    adm.graph.table.accounted_bytes = 20_000          # over budget: halve, repeatedly
    assert adm._live_window() == 8
    assert adm._live_window() == 4
    assert adm._live_window() == 2
    assert adm._live_window() == 1
    assert adm._live_window() == 1, "a loop must always be able to open one body"
    assert adm.min_window_seen == 1

    adm.graph.table.accounted_bytes = 1_000           # comfortable: recover slowly
    assert adm._live_window() == 2
    assert adm._live_window() == 3

    adm.graph.table.accounted_bytes = 6_000           # under budget but not comfortable
    assert adm._live_window() == 3, "no growth without real headroom"

    adm._window_now = adm.window
    adm.graph.table.accounted_bytes = 1_000
    assert adm._live_window() == 16, "never grows past the configured window"


@pytest.mark.unit
def test_shrunken_window_still_caps_a_single_loop() -> None:
    """The adaptive window is the admission cap, not a hint."""
    adm, job = _admission_with(0, qsize=0, workers=4, hard=100_000, idle=False)
    adm.soft_live_bytes = 10_000
    adm.graph.table.accounted_bytes = 20_000
    adm._window_now = 4
    job.in_flight = 1
    assert adm._has_room(job) is True, "window halved 4 -> 2, one body still fits"
    # That call halved the window again (2 -> 1), so the same in_flight no longer fits.
    assert adm._has_room(job) is False, "in_flight has reached the shrunken window"


@pytest.mark.unit
def test_disk_tier_never_evicts_the_payload_a_live_value_waits_on(tmp_path) -> None:
    """The spill valve's other half: RAM can only drop a value once its disk copy
    exists, so the disk tier's own budget enforcement must not evict that copy.

    Measured without this guard: a 369-case sweep filled its cache budget, the
    last-resort "evict live values" path then took back the payloads the engine
    was about to free RAM for, the candidate queue read empty at 50 GB resident,
    and the run had to be killed short of the OOM killer.
    """
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"), max_bytes=4096)
    try:
        waited_on = "ram-is-waiting-on-me"
        backend.set_spill_guard(lambda node_id: node_id == waited_on)
        backend.put_success(waited_on, b"y" * 4000, {"source": "spill"}, 0.0)
        for index in range(6):                     # push the tier well over budget
            backend.put_success(f"filler{index}", b"z" * 4000, {"source": "runtime"}, 0.0)

        assert backend.has(waited_on), "the guarded payload must survive eviction"
    finally:
        backend.close()


@pytest.mark.unit
def test_cache_budget_is_sized_from_free_disk_not_a_fixed_number(tmp_path) -> None:
    """A fixed cache budget cannot be right: this tier is the engine's spill
    space, so a budget smaller than a run's working set breaks the memory bound
    on a machine with terabytes free."""
    from voxlogica.storage import _auto_cache_max_bytes

    sized = _auto_cache_max_bytes(tmp_path)
    assert sized >= 32 * 1024 ** 3, "never below the floor, even on a full disk"
    free = shutil.disk_usage(tmp_path).free
    assert sized <= max(32 * 1024 ** 3, free), "never promises more than the disk has"

    # An explicit budget still wins — the automatic size is only the default.
    explicit = SQLiteResultsDatabase(db_path=str(tmp_path / "explicit.db"), max_bytes=4096)
    try:
        assert explicit._max_bytes == 4096
    finally:
        explicit.close()


@pytest.mark.unit
def test_fused_cone_interiors_do_not_leak_their_values() -> None:
    """A fusion cone's interior may hold a value; dropping its refcount entry
    without evicting leaks that value for the whole run.

    Such a value is resident with NO consumer, so `release` never fires for it
    and the reclaim-candidate rule (consumers > 0) never admits it either — it
    is unreachable by every memory mechanism the engine has. Measured on a
    one-case sweep before this fix: 30.7 GB resident with ZERO eviction
    candidates, dominated by the operators fusion folds into cones
    (vox1.dt 8.6 GB, vox1.mask 8.6 GB).
    """
    from voxlogica.engine.graph import DependencyGraph

    table = NodeTable(backend=None)
    graph = DependencyGraph(table)

    for nid, operator in (("interior", "vox1.dt"), ("exit", "vox1.and")):
        table.nodes[nid] = NodeSpec(kind="primitive", operator=operator)
    table.set_value("interior", b"x" * 4096)      # e.g. rematerialized for an earlier dispatch
    graph.pin("interior")                          # the cone member that consumes it
    assert table.live_bytes == 4096

    graph.complete_cone(["interior"], frozenset({"interior", "exit"}), frozenset({"interior"}))

    assert "interior" not in table.values, "a fused interior's value must not outlive its cone"
    assert table.live_bytes == 0
    assert "interior" not in graph.consumers

    # A protected value (a goal) is still exempt — results must survive.
    table.nodes["goal"] = NodeSpec(kind="primitive", operator="vox1.dt")
    table.set_value("goal", b"y" * 2048)
    graph.pin("goal")
    graph.protected.add("goal")
    graph.complete_cone(["goal"], frozenset({"goal"}), frozenset({"goal"}))
    assert "goal" in table.values, "a goal's value must never be evicted"


@pytest.mark.unit
def test_a_pinned_candidate_is_deferred_not_discarded(tmp_path) -> None:
    """A dispatch pin is transient, so it must defer reclaim, never cancel it.

    Dropping the id while pinned silently retires that value from every memory
    mechanism the engine has — and with each worker pinning its deps on every
    dispatch, the candidate set drains to empty under load. Measured: 0
    candidates against 36 GB resident, dominated by dt/mask.
    """
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        _completed_node(table, "n1", b"x" * 1000)
        engine = _engine_stub(table, max_live_bytes=1)
        engine.graph.consumers["n1"] = 1
        engine._evict_candidates.append("n1")
        engine._dispatch_pins["n1"] = 1            # a pool thread is reading it

        engine._reclaim_memory()

        assert "n1" in table.values, "must not be evicted while pinned"
        assert list(engine._evict_candidates) == ["n1"], "must stay a candidate for later"

        engine._unpin_dispatch(["n1"])
        engine._reclaim_memory()
        assert "n1" not in table.values, "reclaimable again once the dispatch finishes"
        assert engine._evicted_early == 1
    finally:
        backend.close()


@pytest.mark.unit
def test_recompute_scaffolding_is_freed_not_stranded() -> None:
    """Rebuilding an evicted value drags in subtrees nothing else wants.

    Such a child has a zero consumer count, so `release` can never fire for it
    and the reclaim rule (consumers > 0) never offers it either — it is resident
    with no owner. Measured before this fix: 37 GB live with BOTH reclaim queues
    empty, dominated by recompute intermediates (vox1.dt 10 GB, vox1.mask 10 GB).
    """
    from voxlogica.engine.graph import DependencyGraph

    table = NodeTable(backend=None)
    engine = _engine_stub(table, max_live_bytes=1)
    engine.graph = DependencyGraph(table)
    engine._recomputes = 0
    for nid, operator in (("child", "vox1.dt"), ("parent", "vox1.and")):
        table.nodes[nid] = NodeSpec(kind="primitive", operator=operator)
    engine.graph.register_dependency = None  # unused here; deps come from the stub below
    engine.graph._deps_memo = {"parent": ("child",)}
    engine.graph.deps = lambda nid: engine.graph._deps_memo.get(nid, ())

    computed = {}

    def fake_compute(_table, nid):
        # The child must be resident while its parent is being rebuilt.
        computed[nid] = "child" in table.values
        return b"v" * 512

    engine.executor = types.SimpleNamespace(_compute=fake_compute)

    value = ComputationEngine._rematerialize(engine, "parent")

    assert value == b"v" * 512
    assert computed["parent"] is True, "scaffolding must be live DURING the recompute"
    assert "parent" in table.values, "the value actually asked for stays"
    # Over budget (max_live_bytes=1): freed AT THE POINT OF CREATION, not queued.
    # Queueing it cannot bound the tail — one `_rematerialize` recursion
    # materializes a whole subtree inside ONE worker turn while the
    # 256-per-sweep reclaim only runs BETWEEN turns. Measured with queueing
    # alone: census ownerless=32.9G of a 38.0 GB peak against a 25 GB budget,
    # every other bucket bounded.
    assert "child" not in table.values, "pressure frees what nothing will ask for again"

    # Under budget it is CACHED instead: evicting eagerly regardless of pressure
    # rebuilt the same values moments later (19,066 recomputes in 83,701 nodes
    # against a 1.4% baseline). Free RAM is the best cache available.
    table.values.pop("parent", None)
    engine.config.max_live_bytes = 1_000_000_000
    ComputationEngine._rematerialize(engine, "parent")
    assert "child" in table.values, "with room to spare, scaffolding is cached"
    assert "child" in engine._evict_candidates, "and stays reclaimable"


@pytest.mark.unit
def test_payload_writes_are_atomic_so_a_killed_run_cannot_poison_the_cache(tmp_path) -> None:
    """A cache must never poison the run that inherits it.

    A plain write leaves a TRUNCATED payload when the process dies partway
    through — and these processes do die (OOM killer, SIGSEGV, ^C). The next run
    then finds a complete-looking row pointing at a partial file: observed once
    as "cannot reshape array of size 0", and as native crashes seconds into
    several runs that reused a killed run's store.
    """
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    try:
        payload_dir = backend.payload_dir
        # A writer killed mid-write leaves only its .part file, never a partial
        # payload under the real name.
        boom = RuntimeError("killed mid-write")

        original = pathlib.Path.write_bytes

        def explode(self, data):  # noqa: ANN001
            original(self, data)          # the .part file is created ...
            raise boom                    # ... and then we die

        pathlib.Path.write_bytes = explode
        try:
            with pytest.raises(RuntimeError):
                backend._write_payload_atomically("node.bin", b"z" * 512)
        finally:
            pathlib.Path.write_bytes = original

        assert not (payload_dir / "node.bin").exists(), "no partial payload under the real name"
        assert not list(payload_dir.glob("*.part")), "and no debris left behind"

        # A stale .part from a previous kill is purged when the store is opened.
        (payload_dir / "orphan.bin.999.part").write_bytes(b"junk")
        assert backend._purge_partial_payloads() == 1

        # The normal path still round-trips.
        backend._write_payload_atomically("good.bin", b"data")
        assert (payload_dir / "good.bin").read_bytes() == b"data"
    finally:
        backend.close()
