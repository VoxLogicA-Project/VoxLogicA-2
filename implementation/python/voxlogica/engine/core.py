"""The live computation engine — a thin coordinator over four cohesive parts.

A query submits a goal node; the goal's unmaterialized subgraph is registered
with the dependency graph (``engine/graph.py``: Kahn-style pending counts +
consumer refcounts), ready nodes drain through a priority queue
(``engine/ready.py``) into worker coroutines that run kernels on a thread pool,
runtime loops unroll incrementally under windowed backpressure
(``engine/admission.py``), and the disk cache's eviction preference reads an
O(1) liveness probe (``engine/liveness.py``). Queries sharing subexpressions
share work automatically (Merkle identity); a higher-priority query lifts its
dependencies above older work.

THE THROUGHPUT CONTRACT: nothing on the event loop is O(plan) or blocks on
I/O. Per-completion work is O(node degree); cache membership is an in-memory
set; liveness is incremental; expansion is chunked and off-loop. Per-node
scheduling state is dropped at completion, so the working set tracks the
*frontier* — the same run costs the same per node whether the plan holds ten
thousand nodes or ten million.

Coordination runs on one event loop (single-writer over the scheduling maps, no
locks); only primitive kernels and expansion chunks run off-thread.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
import time
from collections import defaultdict, deque
from typing import Any

from tqdm import tqdm

from voxlogica.engine.admission import LoopAdmission
from voxlogica.engine.bandwidth import BandwidthMeter, measure_ceiling_bytes_per_s
from voxlogica.engine.concurrency_probe import ConcurrencyProbe
from voxlogica.engine.config import EngineConfig
from voxlogica.engine.executor import Executor
from voxlogica.engine.expander import Expander
from voxlogica.engine.calibration import load_cached_itk_threads
from voxlogica.buffer_pool import pool_stats, set_limit_bytes, trim_pool
from voxlogica.diagnostics.exceptions import NodeExecutionError
from voxlogica.engine.fusion import FusionPlanner
from voxlogica.engine.itk_threads import apply_itk_threads
from voxlogica.engine.graph import DependencyGraph
from voxlogica.engine.liveness import LivenessProbe
from voxlogica.engine.memlog import MemoryLogger
from voxlogica.engine.node_table import NodeTable
from voxlogica.engine.numba_fusion import NumbaFusionBackend
from voxlogica.engine.topology import default_concurrency
from voxlogica.engine.priority import Priority
from voxlogica.engine.query import Query, QueryStatus
from voxlogica.engine.ready import ReadyQueue
from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import StorageBackend

# Operators whose result is a sequence produced by a (possibly runtime-unrolled)
# loop. Persisting one of these prunes its whole subtree on a warm re-run.
_SEQUENCE_OPERATORS = {"default.sequence", "sequence", "default.map", "map",
                       "default.for_loop", "for_loop", "default.filter", "filter"}

_PROGRESS_BATCH = 64  # completions folded into one progress-bar refresh

#: Span of the sliding throughput window, in seconds (see _flush_progress).
#: Long enough that one slow kernel does not make the figure jitter, short
#: enough that it leaves a multi-minute startup prologue behind on its own
#: rather than averaging it in for the rest of the run.
_RATE_WINDOW_S = 60.0

#: Span of the sliding window the ETA is extrapolated from, in seconds. Goals
#: are coarse -- minutes apart on a large sweep -- so this is much longer than
#: the throughput window: it must hold several of them to say anything, while
#: still being short enough to forget a warm store's opening burst.
_ETA_GOAL_WINDOW_S = 900.0

#: Goals closing in ONE refresh that mark a cache burst rather than progress.
#: Refreshes are sub-second, so computed goals never arrive this fast; a warm
#: store answering hundreds at once does.
_ETA_GOAL_JUMP = 8

_EVICT_SWEEP = 256      # candidates examined per _reclaim_memory call (bounds the work)

# Compact one-line bar: a small FIXED-width bar ({bar:12}) so it never balloons
# to fill the terminal (which, with the goal count sitting at 0 for a long time,
# pushed the useful counters onto a wrapped line). Everything informative — goal
# count, elapsed<ETA, and the live node/rate readout — stays inline on one row.
# The dynamic readout rides in {desc} (not {postfix}: tqdm hardcodes a ", "
# prefix into {postfix}, which printed a stray comma before the operator).
_PROGRESS_FORMAT = "goals: {n:>3}/{total} |{bar:12}| {elapsed} · {desc}"


class ComputationEngine:
    """A persistent, content-addressed, priority-scheduled evaluator."""

    def __init__(self, registry: PrimitiveRegistry | None = None,
                 backend: StorageBackend | None = None, max_concurrency: int = 0,
                 progress: bool = False, debug: bool = False, max_live_bytes: int = 0,
                 threads_auto: str = "balanced"):
        self.registry = registry or PrimitiveRegistry()
        self.table = NodeTable(backend=backend)
        # See engine/topology.py: os.cpu_count() overcounts on a hybrid P/E
        # CPU (measured: E-cores ~0.70x a P-core here, and this workload's
        # memory bandwidth ceiling saturates well before all logical CPUs are
        # busy -- 24 threads is measurably slower AND ~2x the CPU-seconds of
        # 16 on the box this was found on). threads_auto="logical" restores
        # the plain os.cpu_count() default; max_concurrency (--threads N)
        # always overrides both.
        self.max_concurrency = max_concurrency or default_concurrency(threads_auto)
        # A `cores // max_concurrency` FORMULA for ITK's own thread count was
        # tried and reverted: measured on fmt-5000 it was up to 3.1x SLOWER
        # than leaving ITK alone (this system tolerates oversubscription
        # cheaply but not fine-grained subdivision, and the optimum crosses
        # over with worker count -- itk=24 wins at 8 workers, itk=1 wins at
        # 18). No formula is right; see manuscripts/engine-scaling-2026-07.md
        # Part I sec 4-5 and Part II sec 10-11 for why it can't be one.
        # calibration.py now MEASURES it instead, at whatever worker count it
        # settles on, and caches it fingerprinted the same way as the worker
        # count itself. Only apply the cached value when max_concurrency is
        # EXACTLY the worker count it was measured at -- a value calibrated at
        # one worker count is not known-good at another (that crossover is the
        # whole reason a formula failed), so any other worker count (e.g. an
        # explicit --threads N calibration never swept) leaves ITK at its own
        # default: the measured-safe fallback, never the worst option across
        # any worker count in Part I's sweep, just not always the best.
        itk_threads = load_cached_itk_threads(self.max_concurrency)
        if itk_threads is not None:
            apply_itk_threads(itk_threads)
        self.config = EngineConfig.from_env(self.max_concurrency, max_live_bytes)
        self.executor = Executor(self.registry, self.max_concurrency)
        self.expander = Expander(self.table, self.registry)
        self.fusion = FusionPlanner(self.registry)
        self.numba_backend = (
            NumbaFusionBackend(self.registry, min_members=self.config.numba_min_members)
            if self.config.numba_fusion_enabled else None
        )
        # Identity, not a bool: callers (tests, and any warm-reuse-across-runs
        # caller) legitimately replace ``self.numba_backend`` post-construction
        # with one SHARED across several engine instances/run() calls, to reuse
        # its compiled-shape cache. Shutting down a borrowed backend at the end
        # of THIS run() would pull it out from under the other instance(s) still
        # using it -- comparing identity against what THIS engine actually
        # constructed is what tells "mine, shut it down" apart from "borrowed,
        # leave it running" without adding a second public attribute to track.
        self._own_numba_backend = self.numba_backend
        self._show_progress = progress
        self._debug = debug
        self._progress: tqdm | None = None
        self._memlog: MemoryLogger | None = None
        # Progress is reported over GOALS (a fixed, monotonic denominator known at
        # run start), not over nodes: the node total is data-dependent — loops
        # unroll at runtime, so `registered_total` grows for the whole run and a
        # node-fraction bar regresses on every refresh (the "dancing" bar). Goals
        # are near-uniform work, so their count gives a stable bar and an honest
        # ETA. Node throughput is surfaced in the postfix as the liveness signal
        # between goal completions.
        self._progress_pending = 0     # node completions since the last postfix refresh
        self._progress_op = ""         # most recent operator, shown in the postfix
        self._nodes_done = 0           # cumulative node completions (postfix counter)
        self._progress_start = 0.0     # perf_counter at bar creation, for the node rate
        # The rate is measured over a SLIDING window, not from any fixed origin:
        # see _flush_progress. Samples are (perf_counter, _nodes_done) pairs,
        # trimmed to the last _RATE_WINDOW_S seconds on every refresh.
        self._rate_samples: deque[tuple[float, int]] = deque()
        # (perf_counter, goals completed) over the ETA window; see _flush_progress.
        self._goal_samples: deque[tuple[float, int]] = deque()

        # ── The four parts (all event-loop owned; see module docstrings) ──
        self.graph = DependencyGraph(self.table)
        self.ready = ReadyQueue()
        self.liveness = LivenessProbe(self.graph)
        self.liveness.install(self.table._backend)
        self.admission = LoopAdmission(
            self.expander, self.graph, self.ready, self.liveness,
            window=self.config.loop_window,
            chunk=self.config.expansion_chunk or self.config.loop_window,
            workers=self.max_concurrency,
            hard_live_bytes=self.config.hard_live_bytes,
            soft_live_bytes=self.config.max_live_bytes,
            schedule=self._schedule_subgraph,
            available=self._available,
            materialize=self._rematerialize,
            idle=self._idle,
            on_spliced=self._on_spliced,
            fail_node=self._fail_node,
            reclaim=self._reclaim_memory,
        )
        # Pool sized from the memory budget (10%) instead of a fixed 512 MB.
        # This workload is nearly single-shaped, so the flat 16-per-key count,
        # not the byte budget, was what sent freed volumes back to the
        # allocator: measured on the 369 sweep, the pool pinned at 510.9 of
        # 512 MB with 3,906 drops and the kernel spent 20.5% of the machine
        # (~5 cores) servicing 968,846 minor page faults/s re-zeroing pages
        # for volumes the pool had just released. Enabling this was previously
        # blamed for the persister SIGSEGV and backed out; that crash is now
        # root-caused (ITK-owned payload alias, fixed by the event-loop
        # snapshot in engine/persist.py) and the pool is exonerated.
        set_limit_bytes(int(self.config.max_live_bytes * 0.1))

        # ── Queries / goals ──
        self._goals: set[NodeId] = set()
        self._queries: list[Query] = []
        self._query_ids = itertools.count()
        self._waiters: dict[NodeId, list[Query]] = defaultdict(list)
        self._first_error: BaseException | None = None

        # ── Per-node scheduling extras (pruned at completion) ──
        self._priority: dict[NodeId, int] = {}
        self._alias: dict[NodeId, NodeId] = {}      # a loop node -> its spliced sequence node
        self._reload_deferred: set[NodeId] = set()  # deferred once to prefer resident-ready work

        # ── Cache-admission policy + metrics ──
        # Goal dependencies are the reuse "cut": persisting them prunes whole
        # subtrees on a warm re-run (see _is_critical).
        self._critical_nodes: set[NodeId] = set()
        self._peak_frontier = 0     # max in-flight (registered-but-incomplete) nodes
        self._kernels_executed = 0  # kernels run this session (cold high; warm ~0 = full reuse)
        self._recomputes = 0        # evicted values that had to be recomputed, not reloaded
        self._in_flight = 0         # kernels currently executing (watchdog: 0 + no progress = deadlock)
        self._probe: ConcurrencyProbe | None = None  # set for the duration of run()

        # ── Schedule-time fusion (engine/fusion.py) ──
        self._cones_dispatched = 0  # number of cone dispatches (>=2 members each)
        self._ops_fused = 0         # total nodes absorbed into a cone beyond its seed
        self._interiors_elided = 0  # cone members whose value/persist/progress bookkeeping was skipped
        self._cones_numba = 0       # cone dispatches that ran the Stage B compiled kernel

        # ── Proactive reclaim: bounds the sequence-assembly floor ──
        # A completed node with unrun consumers stays refcount-pinned until its
        # LAST consumer runs (see graph.release) — for a wide loop whose
        # sequence node needs every body, that means every completed body's
        # value stays resident for the *entire* unroll, independent of the
        # admission window: peak RSS ~ element count x body size, not bounded
        # by concurrency. Once a value is durably persisted, that RAM copy is
        # no longer the only copy, so under memory pressure it can be evicted
        # early — its eventual consumer reloads it via `_rematerialize` (same
        # path an ordinary evicted dependency already uses). See
        # `_reclaim_memory`.
        # This queue must be lossless while values remain resident. A former
        # 200k-entry cap discarded the oldest candidates permanently; wide
        # sweeps then retained those image values forever and could exceed the
        # hard memory ceiling. The deque stores references to node ids already
        # owned by the table, so retaining every reclaimable value is much
        # cheaper than losing even one image-valued candidate.
        self._evict_candidates: deque[NodeId] = deque()
        # Values whose write is in flight: the only ones a sweep can actually
        # free, kept on their own queue so they are never starved behind the
        # much larger not-yet-spilled backlog (see _reclaim_memory PASS 1).
        self._spill_pending: deque[NodeId] = deque()
        # Ownerless values (recompute scaffolding whose consumers already ran).
        # Freeing one costs NOTHING — no write, and no future read to satisfy —
        # whereas evicting a value that still has a consumer buys the same bytes
        # at the price of a recompute. Sharing one FIFO inverted that priority:
        # the sweep spent its 256-per-turn budget evicting consumer-holding
        # values while free garbage sat deeper in the queue. Measured: 23.3 GB
        # ownerless pinned at the budget line with throughput collapsing from
        # 345 to 100 node/s as the engine recomputed around it.
        self._ownerless: deque[NodeId] = deque()
        # Bytes held by that queue, maintained incrementally so the cap below is
        # an O(1) test rather than a walk.
        self._ownerless_bytes = 0
        # Ownerless values are SPECULATIVE cache: they pay off only if a sibling
        # recompute wants the same subtree again. Values with a pending consumer
        # are a CERTAIN future read. Letting speculation take the whole budget
        # inverts that: measured, ownerless grew to 23.1 GB of a 25 GB budget,
        # evicting certain-read values to make room, which recomputed, which
        # produced more scaffolding — 246,318 recomputes, the highest of any run,
        # and a 3% net slowdown even though peak memory was bounded correctly.
        # Their buffers are far more useful in the pool, where ANY allocation of
        # the same shape can reuse them, than pinned to one node id.
        self._ownerless_share = 0.25
        self._evicted_early = 0    # values evicted proactively (metrics)
        self._spilled_early = 0    # values force-written under pressure so they COULD be evicted
        # Engine-visible traffic (kernel inputs read + output written). Two integer
        # adds per completion; see engine/bandwidth.py for why this is the honest
        # lower bound and how it turns "we are bandwidth-bound" into a measurement.
        self._bandwidth = BandwidthMeter()
        # In-flight read guard: `_reclaim_memory` evicts a value that still has
        # an unmet consumer (that consumer just hasn't *run* yet) as long as
        # it's durably persisted — correct when the eventual read is still in
        # the future, but a live race when a pool thread is reading that exact
        # value RIGHT NOW: rematerialize (event loop) happens-before dispatch,
        # but the pool thread's actual `table.values[dep_id]` lookup can land
        # at any point during the `await`, on a different OS thread, with no
        # lock between it and this coroutine's own eviction sweep. A dep can
        # be rematerialized, then evicted again by another worker's turn,
        # before the dispatch that just rematerialized it ever reads it —
        # `KeyError` in the executor. Pinning a node's resident deps for the
        # exact span of one dispatch (set right after rematerializing, cleared
        # in the dispatch's `finally`) closes the window without touching the
        # refcount-based release path, which is race-free by construction
        # (`graph.release` only fires after the consumer has *finished*, i.e.
        # strictly after its read).
        self._dispatch_pins: dict[NodeId, int] = defaultdict(int)

    # ── Public API ──────────────────────────────────────────────────────────────────────────

    def adopt_plan(self, plan: SymbolicPlan) -> None:
        """Intern a reduced plan's nodes into the table (hash-consed)."""
        self.registry.apply_imports(plan.imported_namespaces)
        self.registry.reset_runtime_state()
        for node_id, node in plan.nodes.items():
            if node_id not in self.table.nodes:
                self.table.nodes[node_id] = node
                self.table.record_lineage(node_id)   # static plan nodes
        self.table.flush_lineage()

    def submit(self, node_id: NodeId, operation: str = "value", name: str = "",
               priority: Priority = Priority.NORMAL) -> Query:
        """Register a goal and schedule its unmaterialized subgraph."""
        query = Query(id=next(self._query_ids), node_id=node_id, operation=operation,
                      name=name, priority=priority)
        self._queries.append(query)
        self._goals.add(node_id)
        self.graph.protected.add(node_id)      # a goal's value survives its last consumer
        self.liveness.unsettled_goals.add(node_id)
        # A goal's direct dependencies are the reuse "cut": persisting them means a
        # warm re-run prunes (and reloads) their whole subtrees instead of
        # recomputing. They are typically cheap (a per-case result), so this is
        # near-free to persist yet collapses the entire computation on re-run.
        self._critical_nodes.update(self.graph.deps(node_id))
        self._waiters[node_id].append(query)
        query.status = QueryStatus.RUNNING
        self._schedule_subgraph(node_id, int(priority))
        if self.table.has_value(node_id):
            self._settle_node(node_id)
        return query

    def prioritize(self, query: Query, priority: Priority) -> None:
        """Raise a query and its unfinished dependencies above lower work."""
        query.priority = priority
        self._raise_priority(query.node_id, int(priority))

    async def run(self) -> None:
        """Drain the ready queue until every admitted unit of work has finished."""
        if self._show_progress:
            # disable=None auto-disables the bar when stderr is not a TTY
            # (redirected to a file/pipe), keeping logs clean; dynamic_ncols
            # re-reads the terminal width on every refresh so the bar reflows
            # instead of garbling on resize. Total = goal count (fixed); `initial`
            # accounts for any goal already satisfied by a warm cache at submit.
            done_already = sum(1 for q in self._queries if q.status is QueryStatus.DONE)
            self._progress_start = time.perf_counter()
            self._progress = tqdm(total=len(self._queries), initial=done_already,
                                  unit="goal", dynamic_ncols=True,
                                  bar_format=_PROGRESS_FORMAT,
                                  disable=None, file=sys.stderr, leave=True)
        self._memlog = MemoryLogger(self._memory_snapshot)
        self._memlog.start()
        # Records whether the engine actually kept max_concurrency kernels busy;
        # see engine/concurrency_probe.py for why wall-clock alone is not enough.
        self._probe = ConcurrencyProbe(lambda: self._in_flight)
        self._probe.start()
        workers = [asyncio.create_task(self._worker()) for _ in range(self.max_concurrency)]
        try:
            await self._join_with_watchdog()
            if self._debug and self.graph.incomplete:
                self._dump_stuck()
        finally:
            for worker in workers:
                worker.cancel()
            self.admission.shutdown()
            self._memlog.stop()
            if self._probe is not None:
                self._probe.stop()
            if self._progress is not None:
                self._flush_progress()
                self._progress.close()
                self._progress = None
        self.table.flush()
        if self._first_error is not None:
            raise self._first_error

    def shutdown(self) -> None:
        """Release this engine's own background resources (currently just its
        numba compile pool, if it has one).

        NOT called automatically at the end of ``run()``: a ``NumbaFusionBackend``
        is explicitly designed to be reused across multiple ``run()`` calls and
        even multiple ``ComputationEngine`` instances (its whole point is a
        compiled-shape cache that survives past any one run — see
        ``numba_fusion.py``'s module docstring). Tying its shutdown to a single
        ``run()``'s ``finally`` block broke exactly that: a caller building a
        second engine on a borrowed backend (``engine.numba_backend = other``)
        would find it shut down and unable to accept new compiles the moment
        the FIRST engine's ``run()`` returned, even though the second engine
        was still actively using it (``RuntimeError: cannot schedule new
        futures after shutdown``).

        Call this once truly done with the engine (e.g. once per CLI
        invocation, after its one-and-only ``run()``) — never call it on a
        backend you didn't construct yourself; it will not be re-created.
        Only shuts down a backend this engine itself constructed (identity
        against ``self._own_numba_backend``, set once in ``__init__``): a
        borrowed/injected backend is never touched here, its shutdown is the
        constructing owner's responsibility.
        """
        if self._own_numba_backend is not None and self.numba_backend is self._own_numba_backend:
            self.numba_backend.shutdown()

    async def _join_with_watchdog(self) -> None:
        """Wait for all work to finish, but NEVER hang silently.

        A bare wait on run-completion would sit forever if a scheduling bug ever
        left work outstanding with no worker able to advance it (the 0%-CPU
        freeze). This watchdog converts any such stall into a loud, diagnosable
        failure: it samples progress, and if no node completes for ``stall``
        seconds it decides whether this is a genuine deadlock (nothing executing,
        nothing ready, no loop mid-expansion — so no amount of waiting will help)
        or merely a very slow kernel, dumping the stuck frontier and raising only
        in the former case. A generous absolute backstop catches a single wedged
        kernel too. Tunable via VOXLOGICA_STALL_TIMEOUT_S (deadlock, default 180)
        and VOXLOGICA_HANG_TIMEOUT_S (wedged-kernel backstop, default 3600).
        """
        join = asyncio.ensure_future(self.ready.wait_idle())
        stall = float(os.environ.get("VOXLOGICA_STALL_TIMEOUT_S", "180"))
        hard = float(os.environ.get("VOXLOGICA_HANG_TIMEOUT_S", "3600"))
        interval = max(1.0, min(15.0, stall / 4.0))
        last_done, idle = -1, 0.0
        while True:
            done, _ = await asyncio.wait({join}, timeout=interval)
            if join in done:
                return
            cur = len(self.table.completed)
            if cur != last_done:
                last_done, idle = cur, 0.0
                continue
            idle += interval
            self._maintain()  # belt: memory-parked work must never be forgotten
            deadlocked = (self._in_flight == 0 and self.ready.qsize() == 0
                          and self.admission.active_jobs == 0)
            if (idle >= stall and deadlocked) or idle >= hard:
                join.cancel()
                self._dump_stuck()
                raise RuntimeError(
                    f"engine stalled: no node completed for {idle:.0f}s "
                    f"({cur} done, in_flight={self._in_flight}, ready={self.ready.qsize()}, "
                    f"jobs={self.admission.active_jobs}, outstanding={self.ready.outstanding}). "
                    f"Stuck frontier dumped above. "
                    f"This is an engine bug (a hang must never happen) — please report; "
                    f"raise VOXLOGICA_STALL_TIMEOUT_S if this was a genuinely slow kernel.")

    # ── Scheduling ──────────────────────────────────────────────────────────────────────────

    def _available(self, nid: NodeId) -> bool:
        """Available = no scheduling needed: done this run, or loadable from disk.

        THE availability rule — every registration path uses this one predicate
        (goals excepted: they are always scheduled so their queries settle
        through the normal completion path).
        """
        return nid in self.table.completed or (nid not in self._goals and self.table.persisted(nid))

    def _schedule_subgraph(self, goal: NodeId, priority: int) -> None:
        """BFS from a goal, pruning at available nodes, registering the rest.

        Two phases: first *discover* the whole unmaterialized subtree (marking
        the frontier), then wire pending counts — a single pass would let a
        parent register before its own dependency was discovered and fire
        early. Constants and closures complete eagerly right here: they need no
        worker, and in loop-heavy plans they are roughly half of all nodes.
        """
        frontier = [goal]
        discovered: list[NodeId] = []
        incomplete = self.graph.incomplete
        completed = self.table.completed
        while frontier:
            nid = frontier.pop()
            if nid in incomplete:
                self._priority[nid] = max(self._priority.get(nid, 0), priority)
                continue
            if nid in completed:
                continue
            if nid not in self._goals and self.table.persisted(nid):
                continue  # cached: loaded on demand
            node = self.table.nodes[nid]
            if node.kind == "constant" and nid not in self._goals:
                self.table.set_value(nid, node.attrs.get("value"))
                self.graph.complete_trivial(nid)
                continue
            if node.kind == "closure":
                # Trivial value, but its captures must stay resident until the
                # loop it gates has fully expanded — per-element bodies read
                # them. The hold is released by the loop's expansion job.
                self.table.set_value(nid, None)
                self.graph.complete_trivial(nid)
                captures = tuple(Expander.closure_capture_ids(node))
                self.admission.hold_captures(nid, captures)
                frontier.extend(captures)
                continue
            incomplete.add(nid)  # mark now; wired below once discovery is complete
            self._priority[nid] = max(self._priority.get(nid, 0), priority)
            discovered.append(nid)
            frontier.extend(self.graph.deps(nid))
        for nid in discovered:
            if self.graph.register(nid):
                self._enqueue(nid)

    def _enqueue(self, nid: NodeId) -> None:
        """Offer a ready node to the workers, or park it under memory pressure.

        Parking keeps a wide fan-out from making the whole DAG resident at
        once; the progress floor in ``_maintain`` guarantees parked work is
        admitted before the workers could ever starve.
        """
        priority = self._priority.get(nid, 0)
        if (self.table.accounted_bytes > self.config.max_live_bytes
                and self.ready.qsize() >= self.max_concurrency):
            self.ready.park(nid, priority)
        else:
            self.ready.push(nid, priority)

    def _memory_snapshot(self) -> dict[str, Any]:
        """One reading for the memory-forensics logger (see engine/memlog.py)."""
        backlog = self.table._persister.pending_bytes if self.table._persister else 0
        return {
            "completed": len(self.table.completed),
            "live_bytes": self.table.live_bytes,
            "backlog_bytes": backlog,
            "accounted_bytes": self.table.accounted_bytes,
            "budget_bytes": self.config.max_live_bytes,
            "hard_bytes": self.config.hard_live_bytes,
            "in_flight": self._in_flight,
            "ready": self.ready.qsize(),
            "parked": self.ready.parked_count,
            "evicted_early": self._evicted_early,
            "evict_candidates": len(self._evict_candidates),
            "spill_pending": len(self._spill_pending),
            "resident_by_op": self.table.resident_by_operator(),
            "bandwidth": self._bandwidth.sample(self.max_concurrency),
            "census": self._resident_census(),
        }

    def _resident_census(self) -> dict[str, int]:
        """Attribute every resident byte to the reason it cannot be freed yet.

        The memlog's `resident_by_op` column answers "what is memory full OF";
        this answers "WHY can none of it leave" — the question every tail
        overshoot investigation has had to reconstruct by hand. Buckets are
        disjoint, first match wins:

        - goal:       run outputs, held until the end by design
        - pinned:     a dispatch is reading it right now (transient)
        - ownerless:  zero consumers — pure reclaimable garbage; a large number
                      here means the drop path is not keeping up
        - durable:    on disk, evictable NOW — large means the sweep lags
        - write_queued: write in flight — bounded by the writer backlog budget
        - undurable:  cheap, recompute-evictable under pressure — the bucket
                      the old requeue-forever policy let grow without bound
        - untracked / untracked_n: resident but on NEITHER reclaim queue — a
                      leak detector; should stay near zero

        Runs on the memlog thread against live dicts: snapshots and broad
        exception handling make it best-effort, per memlog's contract.
        """
        try:
            tracked = (set(self._evict_candidates) | set(self._spill_pending)
                       | set(self._ownerless))
            buckets = {"goal": 0, "pinned": 0, "ownerless": 0, "durable": 0,
                       "write_queued": 0, "undurable": 0,
                       "untracked": 0, "untracked_n": 0}
            for nid in list(self.table.values.keys()):
                size = self.table._sizeof.get(nid, 0)
                if nid in self._goals:
                    buckets["goal"] += size
                    continue
                if self._dispatch_pins.get(nid, 0) > 0:
                    buckets["pinned"] += size
                elif self.graph.consumers.get(nid, 0) <= 0:
                    buckets["ownerless"] += size
                elif self.table.persisted(nid):
                    buckets["durable"] += size
                elif nid in self.table._write_queued:
                    buckets["write_queued"] += size
                else:
                    buckets["undurable"] += size
                if nid not in tracked:
                    buckets["untracked"] += size
                    buckets["untracked_n"] += 1
            return buckets
        except Exception:  # noqa: BLE001 — observability must never break the run
            return {}

    def _idle(self) -> bool:
        """True when nothing is running and nothing is ready — a true wedge.

        The admission hard-ceiling escape and the park floor both consult this:
        it is the one condition under which admitting past the memory ceiling is
        mandatory, because otherwise the run would hang forever.
        """
        return self._in_flight == 0 and self.ready.qsize() == 0

    def _maintain(self) -> None:
        """Admit memory-parked work as budget frees, using the true resident total.

        Uses ``accounted_bytes`` (live tier + persist backlog), so a growing
        write backlog throttles admission instead of silently inflating RSS. The
        progress floor still guarantees a parked node is admitted when the queue
        would otherwise starve — bounded by the same hard ceiling as loop bodies,
        with the true-wedge escape so it can never deadlock.
        """
        if self.table.accounted_bytes > self.config.max_live_bytes:
            trim_pool(0)
        self._reclaim_memory()
        if self.ready.parked_count:
            accounted = self.table.accounted_bytes
            over = accounted > self.config.max_live_bytes and self._first_error is None
            starving = self.ready.qsize() < self.max_concurrency
            if over and accounted >= self.config.hard_live_bytes and not self._idle():
                starving = False  # at the ceiling: hold parked work back, let memory drain
            self.ready.unpark(over_budget=over, starving=starving)
        # Paused unrolls are normally woken by completions, but a worker turn
        # that skips a stale queue entry thins the queue while completing
        # nothing — if only such turns remained, admission would never notice
        # the emerging demand. This runs on every worker turn, so it closes
        # that hole (see LoopAdmission.wake_jobs).
        if self.admission.active_jobs and self.ready.qsize() < self.max_concurrency:
            self.admission.wake_jobs()

    def _reclaim_memory(self) -> None:
        """Evict durably-persisted-but-still-pending values under memory pressure.

        THE VALVE FOR THE SEQUENCE-ASSEMBLY FLOOR: refcounting alone holds a
        loop body's value resident from completion until its *last* consumer
        runs (``graph.release``) — for a wide loop whose sequence node needs
        every body, that means every completed body stays resident for the
        whole unroll. Peak RSS then tracks element count x body size, and no
        admission policy can fix this: admission only gates *new* work: it
        cannot reclaim memory already committed to bodies that finished
        computing and are simply waiting their turn to be assembled.

        Once a value has a durable copy on disk it is no longer the only
        copy, so it is safe to drop the RAM copy early under pressure — its
        eventual consumer transparently reloads it via ``_rematerialize``
        (the same path an ordinary evicted dependency already uses; a miss
        costs one disk read, never a recompute, since we only evict confirmed
        writes). Without a disk backend there is no reload path, so this is a
        no-op — the floor is then a genuine, irreducible requirement of
        materializing every element before combining them.

        Bounded to ``_EVICT_SWEEP`` candidates per call (this runs on every
        worker turn) so it is never an O(plan) scan; a candidate not yet
        durable is requeued once for a later retry.

        Skips anything in ``_dispatch_pins`` (see its definition): a value a
        pool thread is actively reading right now must never be pulled out
        from under it, no matter how much memory pressure there is — pressure
        can wait a few milliseconds for the in-flight read to finish; a
        `KeyError` mid-kernel cannot be undone.
        """
        # NO early return when there is no disk tier. Spilling needs a writer,
        # but dropping an OWNERLESS value does not: nothing will ever read it, so
        # it is free memory even under --no-cache, where this was previously the
        # engine's only reclaim path and it was disabled outright.
        # PASS 1 — values already handed to the writer, waiting to become
        # durable. These are the only ones that can be FREED right now, so they
        # are checked first and on their own queue. Sharing one FIFO with the
        # not-yet-spilled values starved this pass: every sweep re-appended the
        # values it had just spilled, so a durable one sat tens of thousands of
        # entries behind the 256-per-sweep scan window. Measured with the single
        # queue: 110,495 spills against 1,972 evictions — the engine kept paying
        # to write and almost never collected the memory it had bought.
        # The budget is read ONCE per sweep, not once per candidate. It is a
        # heuristic comparison, and re-reading it per iteration meant up to 256
        # reads of accounted_bytes -- each of which used to take the buffer
        # pool's global lock, the same lock every worker needs to allocate or
        # return a buffer. This runs on every worker turn, so that put the event
        # loop in contention with all 16 workers hundreds of times a second.
        budget = self.config.max_live_bytes
        over_budget = self.table.accounted_bytes > budget
        # PASS 0 — free garbage first. An ownerless value costs nothing to
        # release: no write, and no future read to satisfy. Every byte taken
        # here is a byte NOT bought by evicting a value that still has a
        # consumer, which costs a recompute. Draining it behind the general
        # queue inverted exactly that priority and the engine recomputed its
        # way around 23.3 GB of garbage it was holding as "cache".
        # Collect when the run is over budget OR when speculation alone has
        # outgrown its share — the second trigger is what stops garbage from
        # squatting the whole budget while total memory still reads "fine".
        over_share = self._ownerless_bytes > budget * self._ownerless_share
        scanned = 0
        limit = min(len(self._ownerless), _EVICT_SWEEP) if (over_budget or over_share) else 0
        while scanned < limit:
            nid = self._ownerless.popleft()
            self._ownerless_bytes -= self.table._sizeof.get(nid, 0)
            scanned += 1
            if nid not in self.table.values:
                continue                        # already gone
            if self._dispatch_pins.get(nid, 0) > 0:
                self._track_ownerless(nid)      # transient: defer, never discard
                continue
            if self.graph.consumers.get(nid, 0) > 0:
                self._track_evict_candidate(nid)  # gained a consumer: not garbage
                continue
            self._drop_ownerless(nid)
        scanned = 0
        limit = min(len(self._spill_pending), _EVICT_SWEEP) if over_budget else 0
        while scanned < limit:
            nid = self._spill_pending.popleft()
            scanned += 1
            if nid not in self.table.values:
                continue                          # already gone
            if self.graph.consumers.get(nid, 0) <= 0:
                self._drop_ownerless(nid)         # nothing will ever ask for it again
                continue
            if self._dispatch_pins.get(nid, 0) > 0:
                self._spill_pending.append(nid)   # a dispatch is reading it RIGHT NOW: retry later
                continue
            if self.table.persisted(nid):
                self.table.evict(nid)
                self._evicted_early += 1
            else:
                self._spill_pending.append(nid)  # write still in flight; look again later
        # Re-read: PASS 0/1 may have freed enough that no consumer-holding value
        # needs to pay a recompute this turn.
        if not self._evict_candidates or self.table.accounted_bytes <= budget:
            return
        # PASS 2 — everything else: evict what is already durable, and start a
        # write for what is not so a later PASS 1 can free it.
        scanned = 0
        limit = min(len(self._evict_candidates), _EVICT_SWEEP)
        while scanned < limit:
            nid = self._evict_candidates.popleft()
            scanned += 1
            if nid not in self.table.values:
                continue  # already gone
            if self.graph.consumers.get(nid, 0) <= 0:
                self._drop_ownerless(nid)  # ownerless and resident: pure reclaimable garbage
                continue
            if self._dispatch_pins.get(nid, 0) > 0:
                # A dispatch is reading it right now. A PIN IS TRANSIENT, so this
                # must be a deferral, not a verdict: `continue` here dropped the
                # id from the queue for good, and with every worker pinning deps
                # on every dispatch the candidate set drained steadily to empty
                # while the values themselves stayed resident and unreclaimable.
                # Measured: candidates at 0 with 36 GB live, dt/mask dominating.
                self._evict_candidates.append(nid)
                continue
            if self.table.persisted(nid):
                self.table.evict(nid)
                self._evicted_early += 1
            elif (self.table.compute_ms_of(nid) < self.config.persist_min_compute_ms
                  and self._recomputable(nid)):
                # Cheap and not durable: DROP IT — this is the design's actual
                # valve ("a miss falls back to recompute", manuscripts/
                # parallel-engine — eager eviction, admission control, bounded
                # unrolling). The worth-it gate already ruled a rebuild cheaper
                # than a write, so recompute-on-demand (`_rematerialize`) is
                # the exit that costs no bandwidth. Merely requeueing here —
                # "let admission throttle" — re-created the day-one hole in its
                # pure form: admission only gates NEW work, it cannot reclaim
                # bytes already committed to finished values, so the assembly
                # tail's cheap masks were structurally unreclaimable and the
                # eval-30 tail held 36.6 GB against a 25 GB budget (369 cases:
                # grew past 42 GB until killed).
                self.table.evict(nid)
                self._evicted_early += 1
            elif self.table.spill(nid):
                # Expensive but not durable — its completion-time write was
                # skipped (writer saturated at that moment). Recompute would
                # repay the full kernel cost, so a write is the cheaper exit:
                # re-offer it to the writer (spill respects the backlog
                # budget) and let PASS 1 evict it when the write lands.
                self._spill_pending.append(nid)
            else:
                # Writer still saturated: nothing can leave RAM this way right
                # now. Keep the candidate; a later sweep retries.
                self._evict_candidates.append(nid)

    def _recomputable(self, nid: NodeId) -> bool:
        """True iff `_rematerialize` can rebuild this value WITHOUT a disk copy.

        Loop and sequence nodes are computed by the engine's own expansion
        machinery, not by their kernel — `executor._compute` on a `for_loop`
        node raises (the closure argument rematerializes to None by design).
        Such values may only be evicted once durable; everything the executor
        can genuinely re-run (primitives, constants, closures) is fair game
        for evict-and-recompute.
        """
        node = self.table.nodes.get(nid)
        if node is None:
            return False
        if node.kind in ("constant", "closure"):
            return True
        return node.operator not in _SEQUENCE_OPERATORS and not self.expander.can_expand(node)

    def _drop_ownerless(self, nid: NodeId) -> None:
        """Free a resident value that no consumer will ever ask for again.

        Recompute scaffolding lands here: it is materialized to rebuild
        something else, and once that is done nothing holds a reference to it.
        Treating this case as "skip" (it was) made such a value invisible to
        every memory mechanism at once — `release` cannot fire without a
        consumer, and the candidate rule requires one. Under pressure it is free
        memory: no write is needed, because nothing will read it, and if some
        later query does want it, content addressing rebuilds or reloads it.
        """
        if self._dispatch_pins.get(nid, 0) > 0 or nid in self._goals:
            return
        self.table.evict(nid)
        self._evicted_early += 1

    def _track_ownerless(self, nid: NodeId) -> None:
        """Queue free garbage, keeping the byte counter the cap reads in step."""
        self._ownerless.append(nid)
        self._ownerless_bytes += self.table._sizeof.get(nid, 0)

    def _track_evict_candidate(self, nid: NodeId) -> None:
        """Remember a resident value until reclaim or natural release handles it.

        This index is deliberately lossless: dropping an id here can retain the
        corresponding image value indefinitely, which costs far more memory
        than the deque reference and bypasses the engine's hard ceiling.
        """
        self._evict_candidates.append(nid)

    def _pin_dispatch(self, deps) -> None:
        """Protect ``deps`` from proactive eviction for the span of one dispatch.

        Call right after rematerializing a dispatch's dependencies (so the
        values just made resident cannot be evicted again before the pool
        thread that is about to read them actually does); pair with
        ``_unpin_dispatch`` in that dispatch's ``finally``.
        """
        for dep in deps:
            self._dispatch_pins[dep] += 1

    def _unpin_dispatch(self, deps) -> None:
        """Release the hold ``_pin_dispatch`` placed, once the dispatch returns."""
        for dep in deps:
            remaining = self._dispatch_pins[dep] - 1
            if remaining <= 0:
                del self._dispatch_pins[dep]
            else:
                self._dispatch_pins[dep] = remaining

    def _on_spliced(self, loop_id: NodeId, seq_id: NodeId, priority: int) -> None:
        """A loop finished expanding: forward its value from the spliced sequence.

        The loop node re-fires once the sequence completes (or immediately, if
        the sequence was already available) and its worker turn then forwards
        the sequence's value — one extra hold keeps that value resident until
        the forward has happened.
        """
        self._alias[loop_id] = seq_id
        self.graph.pin(seq_id)
        self._priority[seq_id] = max(self._priority.get(seq_id, 0), priority)
        if seq_id in self.graph.incomplete:
            self.graph.await_one(loop_id, seq_id)
        else:
            self.ready.push(loop_id, priority)

    # ── Completion ──────────────────────────────────────────────────────────────────────────

    def _finish(self, nid: NodeId, value: Any, persist: bool = True, compute_ms: float = 0.0,
                skip_enqueue: frozenset[NodeId] = frozenset()) -> None:
        """Record a value, fire dependents, release inputs. O(node degree).

        Constants and closures are trivial and not persisted: a closure exists
        only to force its captures to materialize and to gate its loop; the loop
        reads the closure's structure, never a computed closure value.

        ``skip_enqueue`` exists for fusion cone dispatch only (see
        ``_worker``): a cone member's completion can fire another member of
        the SAME cone that this call's loop has not yet ``_finish``ed itself
        (it is still awaiting its turn in the cone's topological order) — if
        that fired member were pushed onto the ready queue here, another
        worker could pop it and call ``table.begin`` on a node the fusion
        planner already claimed, raising ``DoubleComputationError``. Passing
        the cone's own member set suppresses exactly those enqueues; the
        caller finishes every member itself, in order, so nothing is lost.
        """
        node = self.table.nodes[nid]
        will_be_durable = False
        if persist:
            critical = self._is_critical(nid, node)
            # Best-effort persistence is also *worth-it gated*: serializing a
            # value is GIL-holding Python work, so writing something cheaper to
            # recompute than to store would tax dispatch for nothing (and the
            # cache's cost-aware eviction would drop it first anyway).
            worth_it = critical or compute_ms >= self.config.persist_min_compute_ms
            will_be_durable = self.table.complete(nid, value, compute_ms,
                                                  critical=critical, persist=worth_it)
            if node.operator in _SEQUENCE_OPERATORS:
                for index, item in enumerate(value):
                    self.table.complete_item(nid, index, item)
        else:
            self.table.set_value(nid, value)
            self.table.completed.add(nid)
        # Closures never release their captures here — the loop's expansion job
        # owns that hold (see LoopAdmission.hold_captures).
        for child in self.graph.on_complete(nid, release_inputs=node.kind != "closure"):
            if child not in skip_enqueue:
                self._enqueue(child)
        self._priority.pop(nid, None)
        self.admission.on_complete(nid)
        # EVERY resident value with unrun consumers is a reclaim candidate.
        # This deliberately does NOT pre-filter on durability: a value the
        # worth-it gate declined to persist is not un-reclaimable, it is
        # merely not durable YET, and `_reclaim_memory` can make it durable on
        # demand (NodeTable.spill). Pre-filtering here was the bug: a sweep of
        # cheap large-image kernels persists almost nothing, so the candidate
        # queue ran dry exactly under pressure and the engine had no valve
        # left — 55 GB resident, 12 MB written, 0 candidates, OOM.
        # A value whose write is already in flight goes on the writer-side
        # queue instead, so PASS 1 collects it the moment its write lands —
        # sharing one FIFO buried the durable ones tens of thousands of
        # entries deep (measured: 110,495 spills, 1,972 evictions).
        if nid not in self._goals and self.graph.consumers.get(nid, 0) > 0:
            if will_be_durable:
                self._spill_pending.append(nid)
            else:
                self._track_evict_candidate(nid)
        moved = self.table._sizeof.get(nid, 0)
        for dep in self.graph.deps(nid):
            moved += self.table._sizeof.get(dep, 0)
        self._bandwidth.add(moved)
        frontier = len(self.graph.incomplete)
        if frontier > self._peak_frontier:
            self._peak_frontier = frontier
        self._settle_node(nid)
        if self._progress is not None:
            self._nodes_done += 1
            self._progress_pending += 1
            self._progress_op = node.operator
            if self._progress_pending >= _PROGRESS_BATCH:
                self._flush_progress()

    def _flush_progress(self) -> None:
        """Refresh the postfix (node counter + smoothed rate + current op + ETA).

        The bar's position advances only on goal completion (see
        ``_settle_node``); this call just repaints the postfix so the user sees
        liveness between goals. It never touches ``total`` — that is what kept
        the old node-total bar dancing as the plan expanded.

        tqdm's own ``{remaining}`` token is driven by the bar's ``n`` (goal
        count), which for programs whose goals only settle in a late burst
        (e.g. a per-case sweep whose prints all depend on a shared prefix)
        stays at 0 for most of the run — tqdm then has no rate to extrapolate
        from and prints "<?" for the entire run, even though real progress
        (node throughput) is steady the whole time. Compute the ETA from node
        completion rate instead — always available once any nodes have
        completed — and show it here; ``_PROGRESS_FORMAT`` no longer renders
        tqdm's own ``{remaining}``.

        The current operator name is last and unpadded (its length is
        unbounded — namespaced primitive names vary a lot, e.g. "not" vs
        "vox1.n4"). Every field before it — the nodes counter and the rate —
        has reserved, fixed width, so a long/short op name never shifts them:
        without this, the op name used to sit mid-line and made the ETA/rate
        dance left and right as it changed length every refresh.
        """
        # SLIDING window, not an average from any origin. A single fixed origin
        # -- even one deferred past a warm-up threshold -- still divides by an
        # ever-growing elapsed time, so anything slow that the window has ever
        # contained keeps dragging the figure down for the rest of the run and
        # the number can never recover. On a cold 369-case sweep the per-case
        # n4/border prologue holds ~18 node/s for the first five minutes; with a
        # fixed origin the bar was still reporting ~300 node/s an hour later
        # while the engine was measurably sustaining ~450. The warm-up threshold
        # that origin waited for was 500 nodes, crossed ~30 s into that run --
        # deep inside the prologue -- so the reset did not exclude it either.
        #
        # A window that forgets instead: only the last _RATE_WINDOW_S seconds
        # count, so the figure tracks what the engine is doing NOW and climbs
        # out of any transient on its own. Still an average over that span --
        # it smooths deliberately, so one slow kernel does not make it jitter.
        now = time.perf_counter()
        self._rate_samples.append((now, self._nodes_done))
        cutoff = now - _RATE_WINDOW_S
        # Keep one sample at or before the cutoff so the window spans the full
        # period even when refreshes are sparse; drop everything older.
        while len(self._rate_samples) > 2 and self._rate_samples[1][0] <= cutoff:
            self._rate_samples.popleft()
        t0, done0 = self._rate_samples[0]
        window_s = now - t0
        window_done = self._nodes_done - done0
        # Report over whatever span the window has, from the very first refresh:
        # a partial window is already the right answer for the period it covers,
        # and short-circuiting to a whole-run average would reintroduce exactly
        # the origin this replaced. Only the degenerate first sample (no span,
        # nothing completed) falls back.
        rate = (window_done / window_s) if window_s > 1e-6 else \
               self._nodes_done / max(1e-6, now - self._progress_start)
        elapsed = max(1e-6, now - self._progress_start)
        # `known` = nodes discovered so far (graph.registered_total). It grows
        # monotonically as loops unroll and stops growing once the plan is fully
        # expanded — at which point it IS the true total node count. Shown as a
        # plain counter (done / known), never as a fraction, so it never dances.
        known = self.graph.registered_total
        remaining_nodes = known - self._nodes_done
        # ETA FROM GOALS, not from nodes. `known` counts nodes discovered so far
        # and grows as loops unroll, so `known - done` is the CURRENT frontier,
        # not the remaining work: under dynamic expansion the two grow together
        # and their difference sits near-constant. Measured on a 369-case sweep,
        # it held ~36,500 for six minutes while the plan went 41,755 -> 212,187,
        # and the bar advertised ~2 minutes for hours.
        #
        # The goal count does not have this problem: it is known exactly and up
        # front (it comes from the program's structure, not from unrolling) and
        # only ever rises. Extrapolating elapsed time over the fraction of goals
        # closed gives a figure that is wrong early -- the first goals carry the
        # per-case prologue -- and converges as the run proceeds, which is the
        # opposite of, and strictly better than, being confidently wrong forever.
        #
        # It must also measure only goals THIS run is closing, over a SLIDING
        # window. A warm store satisfies hundreds of goals from cache in the
        # first seconds; anchoring at a fixed origin cannot exclude them, because
        # the bar's first refresh lands at 0/748 BEFORE that burst arrives (seen
        # in the log: "goals: 0/748 | 00:00", then 305/748 by 00:32, and an
        # anchored estimate duly promised the remaining 443 in 48 seconds). A
        # window has no origin to be wrong about: the burst ages out of it.
        #
        # No node-frontier fallback. It is precisely the estimator this replaces,
        # so falling back to it means printing a number already known to be
        # wrong; "?" is the honest reading until the window has evidence.
        # A cache burst is a DISCONTINUITY, not a rate, and waiting for it to age
        # out of the window leaves a quarter of an hour of wrong readings. Goals
        # arriving many-at-once in a single refresh are therefore treated as what
        # they are -- work that did not happen here -- and the window restarts
        # from after them, so a true rate is available within two real goals.
        goals_done = self._progress.n or 0
        goals_total = self._progress.total or 0
        if self._goal_samples and goals_done - self._goal_samples[-1][1] >= _ETA_GOAL_JUMP:
            self._goal_samples.clear()
        self._goal_samples.append((now, goals_done))
        goal_cutoff = now - _ETA_GOAL_WINDOW_S
        while len(self._goal_samples) > 2 and self._goal_samples[1][0] <= goal_cutoff:
            self._goal_samples.popleft()
        gt0, gd0 = self._goal_samples[0]
        goals_in_window = goals_done - gd0
        goal_span = now - gt0
        # Two, not one: one goal gives a rate from a single sample, and goals are
        # coarse enough that a lone one says little about the ones behind it.
        if goals_in_window >= 2 and goal_span > 0 and goals_total > goals_done:
            eta = tqdm.format_interval(
                (goals_total - goals_done) * goal_span / goals_in_window)
        else:
            eta = "?"
        known_str = f"{known:,}"
        done_str = f"{self._nodes_done:,}"
        w = len(known_str)
        rate_str = f"{rate:,.0f}"
        self._progress.set_description_str(
            f"{done_str:>{w}}/{known_str} nodes · {rate_str:>7} node/s · ETA {eta:>9} · {self._progress_op}",
            refresh=False)
        self._progress.refresh()
        self._progress_pending = 0

    def _is_critical(self, nid: NodeId, node) -> bool:
        """Whether a result must be persisted (vs. best-effort) for cross-run reuse.

        The critical set is deliberately small and cheap, yet covers nearly the
        whole DAG on a warm re-run:
        - goal-dependency *cut* nodes — pruning one collapses its entire subtree;
        - structural loop/sequence nodes — same leverage, and they gate re-expansion;
        - widely-shared results (high fan-out) — a per-case image feeding every
          combo, so a *variant* sweep reuses it and recomputes only its changed tail.
        Everything else (large one-shot intermediates) stays best-effort: forcing
        it critical would not aid warm pruning and would pin gigabytes in the
        persist backlog.
        """
        return (nid in self._critical_nodes
                or node.operator in _SEQUENCE_OPERATORS
                or self.graph.consumers.get(nid, 0) >= self.config.persist_fanout)

    def _rematerialize(self, nid: NodeId) -> Any:
        """Recompute (or reload) a completed node whose value was evicted."""
        if nid in self.table.values:
            return self.table.values[nid]
        loaded = self.table.load(nid)
        if loaded is not None:
            self._retrack_resident(nid)
            return loaded
        node = self.table.nodes[nid]
        if node.kind == "constant":
            value = node.attrs.get("value")
        elif node.kind == "closure":
            value = None  # closures are trivial; only their captures carry data
        else:
            # Rebuilding this value may drag in whole subtrees that NOTHING else
            # still wants (their consumers ran long ago and released them). Such
            # a child is resident purely as scaffolding for this one recompute:
            # with a zero consumer count it can never be released by `release`,
            # and `_retrack_resident` will not offer it for reclaim either, so
            # leaving it behind strands it for the rest of the run. Measured on a
            # one-case sweep: 37 GB resident with BOTH reclaim queues empty,
            # dominated by exactly these recompute intermediates (vox1.dt 10 GB,
            # vox1.mask 10 GB). Note the children each hold their own scaffolding
            # transitively, so the deepest recompute frees itself first.
            scaffolding = [child for child in self.graph.deps(nid)
                           if child not in self.table.values
                           and self.graph.consumers.get(child, 0) <= 0]
            for child in self.graph.deps(nid):
                self._rematerialize(child)
            self._recomputes += 1  # an evicted value we could neither find nor reload
            value = self.executor._compute(self.table, nid)
            # Scaffolding disposal is PRESSURE-GATED, and both halves are
            # load-bearing — each was measured by getting it wrong:
            #
            # - Always evicting burns CPU the leak had saved: a sweep's sibling
            #   recomputes share subexpressions, so dropping them here just
            #   rebuilds the same values moments later (19,066 recomputes in
            #   83,701 nodes = 23%, against a 1.4% baseline).
            # - Always tracking cannot bound the tail: a recompute produces
            #   scaffolding faster than a 256-per-sweep reclaim consumes it,
            #   because a single `_rematerialize` recursion materializes a whole
            #   subtree within ONE worker turn while the sweep runs BETWEEN
            #   turns. Measured with tracking alone: the census read
            #   `ownerless=32.9G` of a 38.0 GB peak against a 25 GB budget, with
            #   every other bucket bounded (undurable 1.0G, untracked 0.0G).
            #
            # So: keep it while there is room (free RAM is the best cache we
            # have), drop it the moment there is not — at the point of
            # creation, where no queue latency can let it accumulate.
            over_budget = self.table.accounted_bytes > self.config.max_live_bytes
            for child in scaffolding:
                if over_budget:
                    self._drop_ownerless(child)
                else:
                    # Cached, but on the FREE-GARBAGE queue: PASS 0 collects it
                    # ahead of anything whose eviction would cost a recompute,
                    # and drops it once speculation exceeds its share.
                    self._track_ownerless(child)
        self.table.set_value(nid, value)
        self._retrack_resident(nid)
        return value

    def _retrack_resident(self, nid: NodeId) -> None:
        """Re-arm reclaim for a value just brought BACK into the live tier.

        Eviction consumes a candidate: `_reclaim_memory` pops it and drops the
        RAM copy. When a later consumer reloads that value, it is resident
        again — but it was no longer registered anywhere, so reclaim could
        never touch it a second time. Every evict/reload cycle therefore
        retired one value from the valve's reach permanently, and the candidate
        queue drained to empty exactly when pressure was highest: measured, the
        one-case sweep finished with 44 GB resident and ZERO candidates, its
        whole tail spent reloading values it could no longer release.

        Duplicates in the queue are harmless (a popped id that is gone, or has
        no consumers left, is skipped) — losing an id is not.
        """
        if nid not in self._goals and self.graph.consumers.get(nid, 0) > 0:
            self._track_evict_candidate(nid)

    # ── Workers ─────────────────────────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Pull ready nodes by priority and drive them to completion."""
        while True:
            nid = await self.ready.pop()
            try:
                if self._first_error is not None or nid in self.table.completed:
                    continue  # cancelled, or a duplicate of an already-finished node
                node = self.table.nodes[nid]
                if nid in self._alias:
                    seq_id = self._alias.pop(nid)
                    # persist=True even though seq_id already holds the same
                    # value durably: the loop id is the *statically known*
                    # pruning point — a warm re-run prunes at it and skips
                    # re-expansion entirely (the spliced sequence id is only
                    # discoverable BY re-expanding), and serve/inspect tooling
                    # addresses sequence items by the loop's id. The price is
                    # one duplicated payload per loop in the persist backlog;
                    # those bytes are accounted, so admission absorbs them.
                    self._finish(nid, self._rematerialize(seq_id))  # forward spliced result
                    self.graph.release(seq_id)                      # the forward's hold
                elif nid in self.table.values:
                    # Materialized since this node was enqueued — a warm cache can
                    # fill table.values via load() (disk reload) or a shared path
                    # reaching the same node through another goal. Forward the value
                    # instead of recomputing; recomputing would trip the
                    # single-computation guard in begin().
                    self._finish(nid, self.table.values[nid], persist=False)
                elif self.expander.can_expand(node):
                    # Hand the loop to the admission unit: bodies are reduced in
                    # chunks off-loop and admitted under the window. This turn
                    # ends now; the loop node re-fires via its alias once the
                    # spliced sequence completes.
                    self.admission.start(nid, node, self._priority.get(nid, int(Priority.NORMAL)))
                elif node.kind == "constant":
                    self._finish(nid, node.attrs.get("value"), persist=False)
                elif node.kind == "closure":
                    self._finish(nid, None, persist=False)  # trivial; only its captures matter
                else:
                    # Persistence never throttles compute: when the writer is
                    # behind, NodeTable.complete simply skips caching that value
                    # (best-effort), so the workers keep running at full width and
                    # cache housekeeping stays a background, best-effort activity.
                    # Prefer resident-ready work: if this node would have to reload
                    # an evicted input and there is plenty of other ready work whose
                    # inputs are resident, let that run first (defer once). Keeps the
                    # workers busy on RAM-resident data instead of stalling on I/O.
                    if (nid not in self._reload_deferred
                            and self.ready.qsize() >= self.max_concurrency
                            and any(dep not in self.table.values for dep in self.graph.deps(nid))):
                        self._reload_deferred.add(nid)
                        self.ready.push(nid, self._priority.get(nid, 0))
                        continue
                    self._reload_deferred.discard(nid)
                    for dep in self.graph.deps(nid):
                        if dep not in self.table.values:
                            self._rematerialize(dep)  # recompute deps evicted under pressure

                    # Schedule-time fusion (engine/fusion.py): try to grow a cone
                    # of elementwise consumers seeded at this ready node, so one
                    # thread-pool dispatch replaces several scheduling round trips.
                    # A no-op (returns None) for non-elementwise nodes, when
                    # fusion is disabled, or when no ripe partner is found — the
                    # single-node path below is unchanged in every such case.
                    cone = (self.fusion.plan(nid, graph=self.graph, table=self.table,
                                             goals=self._goals, cap=self.config.fusion_cap)
                            if self.config.fusion_enabled else None)
                    if cone is not None:
                        # Cone inputs may have been evicted since planning looked
                        # at them — the same reload-before-dispatch guarantee the
                        # single-node path gives its own deps, just over the
                        # cone's aggregate external inputs.
                        for dep in cone.inputs:
                            if dep not in self.table.values:
                                self._rematerialize(dep)
                        self._kernels_executed += len(cone)
                        self._cones_dispatched += 1
                        self._ops_fused += len(cone) - 1
                        started = time.perf_counter()
                        self._in_flight += 1
                        # Pin cone.inputs for the span of this dispatch: they were
                        # just rematerialized above, but the pool thread that reads
                        # them runs concurrently with every other worker's turn —
                        # including one that proactively evicts under pressure
                        # (_reclaim_memory). Without this, a dep can be evicted
                        # again before the cone ever reads it. See _pin_dispatch.
                        self._pin_dispatch(cone.inputs)
                        try:
                            # Stage A vs Stage B (engine/numba_fusion.py) is
                            # decided inside the executor, on the pool thread
                            # that runs it — never here on the event loop
                            # (see Executor.run_cone_auto's docstring).
                            results, used_numba = await self.executor.run_cone_auto(
                                self.table, cone, self.numba_backend)
                            if used_numba:
                                self._cones_numba += 1
                        finally:
                            self._in_flight -= 1
                            self._unpin_dispatch(cone.inputs)
                        per_member_ms = (time.perf_counter() - started) * 1000.0 / len(cone)
                        cone_set = frozenset(cone.members_topo)
                        # Interiors (every consumer in-cone, not a goal) are
                        # batch-completed with NO value/persist/progress
                        # bookkeeping — that per-node cost, not the dispatch
                        # round trip Stage A already batched, turned out to
                        # dominate (see frontier-scheduler.md "Semantic
                        # queueing"). Only the graph-level scheduling state
                        # (pending/consumers/incomplete) needs dropping; a
                        # later consumer that genuinely needs an elided
                        # interior's value rematerializes it on demand,
                        # exactly like any other evicted value.
                        if cone.interiors:
                            self.graph.complete_cone(cone.members_topo, cone_set, cone.interiors)
                            self._interiors_elided += len(cone.interiors)
                            for member_id in cone.interiors:
                                self.table.complete_without_value(member_id)
                                self._priority.pop(member_id, None)
                                self.admission.on_complete(member_id)
                            if self._progress is not None:
                                self._nodes_done += len(cone.interiors)
                                self._progress_pending += len(cone.interiors)
                                if self._progress_pending >= _PROGRESS_BATCH:
                                    self._flush_progress()
                            frontier = len(self.graph.incomplete)
                            if frontier > self._peak_frontier:
                                self._peak_frontier = frontier
                        # Exits get the full normal path: value, persist
                        # decision, evict-candidate tracking, settle_node
                        # (goals may be exits), progress. Its own
                        # release-my-deps loop harmlessly no-ops on any
                        # interior it depends on (already dropped above).
                        for member_id in cone.members_topo:
                            if member_id in cone.exits:
                                self._finish(member_id, results[member_id],
                                            compute_ms=per_member_ms, skip_enqueue=cone_set)
                        continue

                    self.table.begin(nid)  # enforces the no-double-computation invariant
                    self._kernels_executed += 1
                    started = time.perf_counter()
                    self._in_flight += 1
                    deps = self.graph.deps(nid)
                    self._pin_dispatch(deps)  # see _pin_dispatch: protects the rematerialize above
                    try:
                        value = await self.executor.run(self.table, nid)
                    finally:
                        self._in_flight -= 1
                        self._unpin_dispatch(deps)
                    # measured recompute cost feeds the cache's cost-aware eviction
                    self._finish(nid, value, compute_ms=(time.perf_counter() - started) * 1000.0)
            except Exception as exc:  # noqa: BLE001
                self._fail_node(nid, exc)
            finally:
                # Admit held-back work before retiring this unit, so the queue
                # is never observed empty while admissible work is parked.
                self._maintain()
                self.ready.end_unit()

    # ── Failure / diagnostics ───────────────────────────────────────────────────────────────

    def _fail_node(self, nid: NodeId, error: BaseException) -> None:
        """First failure wins: record it, fail the node's waiters, drain fast.

        Aborting admission wakes paused expansion jobs so they exit; unparking
        everything lets the workers consume-and-skip the remaining units, so
        ``run`` terminates promptly and reports the error instead of hanging.
        """
        if not isinstance(error, NodeExecutionError):
            node = self.table.nodes.get(nid)
            wrapped = NodeExecutionError(nid, node.operator if node is not None else "<engine>")
            wrapped.__cause__ = error
            error = wrapped
        if self._first_error is None:
            self._first_error = error
            self.admission.abort(error)
            self.ready.unpark(over_budget=False, starving=False)
        self._fail_waiters(nid, error)

    def _dump_stuck(self) -> None:
        """Diagnostic: report frontier nodes that never completed."""
        stuck = list(self.graph.incomplete)
        print(f"[stuck] qsize={self.ready.qsize()} outstanding={self.ready.outstanding} "
              f"completed={len(self.table.completed)} stuck={len(stuck)} "
              f"alias={len(self._alias)} jobs={self.admission.active_jobs}", file=sys.stderr)
        for nid in stuck[:12]:
            node = self.table.nodes[nid]
            unmet = [d[:8] for d in self.graph.deps(nid) if d in self.graph.incomplete]
            print(f"  {nid[:8]} op={node.operator} kind={node.kind} "
                  f"pending={self.graph.pending.get(nid)} alias={nid in self._alias} "
                  f"unmet={unmet}", file=sys.stderr)

    # ── Helpers ─────────────────────────────────────────────────────────────────────────────

    def _raise_priority(self, nid: NodeId, priority: int) -> None:
        """Propagate a priority bump to a node and its unfinished dependencies.

        Walks only the incomplete frontier (already-finished nodes cannot be
        reprioritised, not-yet-admitted ones inherit the raised priority when
        they are scheduled), so the walk is bounded by the frontier size.
        """
        frontier = [nid]
        seen: set[NodeId] = set()
        while frontier:
            current = frontier.pop()
            if current in seen or current not in self.graph.incomplete:
                continue
            seen.add(current)
            self._priority[current] = max(self._priority.get(current, 0), priority)
            frontier.extend(self.graph.deps(current))

    def metrics(self) -> dict[str, Any]:
        """Scheduler/cache statistics for the run summary.

        ``recomputes`` should be ~0: a healthy run computes each node once, so a
        large value signals eviction⇄recompute thrash. ``peak_frontier`` is the
        high-water mark of the open working set — bounded by the admission
        window, *not* by plan size. ``peak_live_bytes`` is the resident
        high-water mark (what admission control bounds).
        """
        m: dict[str, Any] = {
            "max_concurrency": self.max_concurrency,
            # ACHIEVED vs requested concurrency. `saturation` well below 1.0 means
            # the engine was starving (scheduler/dependency bound) and tuning
            # kernels or threads cannot help; near 1.0 means the scheduler did its
            # job and any disappointing wall-clock lies in the kernels or the
            # memory system. Reporting wall-clock without this is what made four
            # separate scaling conclusions unfalsifiable -- see
            # doc/dev/scaling-test-design.md.
            "mean_concurrency": round(
                self._probe.mean_concurrency, 2) if self._probe else 0.0,
            "peak_concurrency": self._probe.peak_concurrency if self._probe else 0,
            "saturation": round(
                self._probe.saturation(self.max_concurrency), 3) if self._probe else 0.0,
            "peak_live_mb": round(self.table.peak_live_bytes / 1024 ** 2, 1),
            "live_budget_mb": round(self.config.max_live_bytes / 1024 ** 2, 1),
            "peak_frontier": self._peak_frontier,
            "loop_window": self.config.loop_window,
            "kernels_executed": self._kernels_executed,
            "recomputes": self._recomputes,
            "expanded_loops": self.admission.expanded_loops,
            "expanded_bodies": self.admission.expanded_bodies,
            "evicted_early": self._evicted_early,
            "spilled_early": self._spilled_early,
            "ceiling_escapes": self.admission.ceiling_escapes,
            "min_loop_window": self.admission.min_window_seen,
            "bytes_moved_gb": round(self._bandwidth.bytes_moved / 1024 ** 3, 1),
            "machine_copy_bandwidth_gbs": round(measure_ceiling_bytes_per_s(self.max_concurrency) / 1024 ** 3, 1),
            "cones_dispatched": self._cones_dispatched,
            "ops_fused": self._ops_fused,
            "mean_cone_size": round(
                1 + self._ops_fused / self._cones_dispatched, 2
            ) if self._cones_dispatched else 0.0,
            "interiors_elided": self._interiors_elided,
            "cones_numba": self._cones_numba,
        }
        if self.numba_backend is not None:
            m["numba_compiles_started"] = self.numba_backend.compiles_started
            m["numba_compiles_finished"] = self.numba_backend.compiles_finished
            m["numba_compiles_failed"] = self.numba_backend.compiles_failed
        buffers = pool_stats()
        m["buffer_pool_allocations"] = buffers["allocations"]
        m["buffer_pool_reuses"] = buffers["reuses"]
        m["buffer_pool_returns"] = buffers["returns"]
        m["buffer_pool_drops"] = buffers["drops"]
        m["buffer_pool_mb"] = round(buffers["pooled_bytes"] / 1024 ** 2, 1)
        m["buffer_pool_peak_mb"] = round(buffers["peak_pooled_bytes"] / 1024 ** 2, 1)
        backend = self.table._backend
        if backend is not None and hasattr(backend, "stats"):
            s = backend.stats()
            m["cache_hits"] = s.get("hits", 0)  # values reloaded from disk (cross-run reuse)
            m["cache_bytes_mb"] = round(s.get("payload_bytes", 0) / 1024 ** 2, 1)
            m["evicted_dead"] = s.get("evicted_dead", 0)
            m["evicted_live"] = s.get("evicted_live", 0)
        return m

    def _settle_node(self, nid: NodeId) -> None:
        """Resolve any queries whose goal node just materialized."""
        waiters = self._waiters.get(nid)
        if waiters:
            self.liveness.unsettled_goals.discard(nid)
            newly = 0
            for query in waiters:
                if query.status is not QueryStatus.DONE:
                    newly += 1              # count RUNNING -> DONE transitions only
                query._settle(QueryStatus.DONE, value=self.table.values.get(nid))
            if self._progress is not None and newly:
                self._progress.update(newly)  # the bar advances one step per goal

    def _fail_waiters(self, nid: NodeId, error: BaseException) -> None:
        """Mark queries on a failed node as failed."""
        for query in self._waiters.get(nid, ()):
            query._settle(QueryStatus.FAILED, error=error)
