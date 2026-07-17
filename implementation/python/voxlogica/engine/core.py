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
from voxlogica.engine.config import EngineConfig
from voxlogica.engine.executor import Executor
from voxlogica.engine.expander import Expander
from voxlogica.engine.graph import DependencyGraph
from voxlogica.engine.liveness import LivenessProbe
from voxlogica.engine.memlog import MemoryLogger
from voxlogica.engine.node_table import NodeTable
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

_EVICT_SWEEP = 256      # candidates examined per _reclaim_memory call (bounds the work)
_EVICT_QUEUE_CAP = 200_000  # backstop on the candidate queue itself (bounds idle-run memory)

# Compact one-line bar: a small FIXED-width bar ({bar:12}) so it never balloons
# to fill the terminal (which, with the goal count sitting at 0 for a long time,
# pushed the useful counters onto a wrapped line). Everything informative — goal
# count, elapsed<ETA, and the live node/rate readout — stays inline on one row.
# The dynamic readout rides in {desc} (not {postfix}: tqdm hardcodes a ", "
# prefix into {postfix}, which printed a stray comma before the operator).
_PROGRESS_FORMAT = "goals: {n:>3}/{total} |{bar:12}| {elapsed}<{remaining} · {desc}"


class ComputationEngine:
    """A persistent, content-addressed, priority-scheduled evaluator."""

    def __init__(self, registry: PrimitiveRegistry | None = None,
                 backend: StorageBackend | None = None, max_concurrency: int = 0,
                 progress: bool = False, debug: bool = False, max_live_bytes: int = 0):
        self.registry = registry or PrimitiveRegistry()
        self.table = NodeTable(backend=backend)
        self.max_concurrency = max_concurrency or (os.cpu_count() or 8)
        self.config = EngineConfig.from_env(self.max_concurrency, max_live_bytes)
        self.executor = Executor(self.registry, self.max_concurrency)
        self.expander = Expander(self.table, self.registry)
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
            schedule=self._schedule_subgraph,
            available=self._available,
            materialize=self._rematerialize,
            idle=self._idle,
            on_spliced=self._on_spliced,
            fail_node=self._fail_node,
        )

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
        self._evict_candidates: deque[NodeId] = deque()
        self._evicted_early = 0    # values evicted proactively (metrics)

    # ── Public API ──────────────────────────────────────────────────────────────────────────

    def adopt_plan(self, plan: SymbolicPlan) -> None:
        """Intern a reduced plan's nodes into the table (hash-consed)."""
        self.registry.apply_imports(plan.imported_namespaces)
        self.registry.reset_runtime_state()
        for node_id, node in plan.nodes.items():
            self.table.nodes.setdefault(node_id, node)

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
            if self._progress is not None:
                self._flush_progress()
                self._progress.close()
                self._progress = None
        self.table.flush()
        if self._first_error is not None:
            raise self._first_error

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
        }

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
        self._reclaim_memory()
        if self.ready.parked_count:
            accounted = self.table.accounted_bytes
            over = accounted > self.config.max_live_bytes and self._first_error is None
            starving = self.ready.qsize() < self.max_concurrency
            if over and accounted >= self.config.hard_live_bytes and not self._idle():
                starving = False  # at the ceiling: hold parked work back, let memory drain
            self.ready.unpark(over_budget=over, starving=starving)

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
        """
        if self.table._persister is None or not self._evict_candidates:
            return
        scanned = 0
        limit = min(len(self._evict_candidates), _EVICT_SWEEP)
        while (scanned < limit
               and self.table.accounted_bytes > self.config.max_live_bytes):
            nid = self._evict_candidates.popleft()
            scanned += 1
            if nid not in self.table.values or self.graph.consumers.get(nid, 0) <= 0:
                continue  # already naturally released since being queued
            if self.table.persisted(nid):
                self.table.evict(nid)
                self._evicted_early += 1
            else:
                self._evict_candidates.append(nid)  # not yet durable; retry later

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

    def _finish(self, nid: NodeId, value: Any, persist: bool = True, compute_ms: float = 0.0) -> None:
        """Record a value, fire dependents, release inputs. O(node degree).

        Constants and closures are trivial and not persisted: a closure exists
        only to force its captures to materialize and to gate its loop; the loop
        reads the closure's structure, never a computed closure value.
        """
        node = self.table.nodes[nid]
        if persist:
            critical = self._is_critical(nid, node)
            # Best-effort persistence is also *worth-it gated*: serializing a
            # value is GIL-holding Python work, so writing something cheaper to
            # recompute than to store would tax dispatch for nothing (and the
            # cache's cost-aware eviction would drop it first anyway).
            worth_it = critical or compute_ms >= self.config.persist_min_compute_ms
            self.table.complete(nid, value, compute_ms, critical=critical, persist=worth_it)
            if node.operator in _SEQUENCE_OPERATORS:
                for index, item in enumerate(value):
                    self.table.complete_item(nid, index, item)
        else:
            self.table.set_value(nid, value)
            self.table.completed.add(nid)
        # Closures never release their captures here — the loop's expansion job
        # owns that hold (see LoopAdmission.hold_captures).
        for child in self.graph.on_complete(nid, release_inputs=node.kind != "closure"):
            self._enqueue(child)
        self._priority.pop(nid, None)
        self.admission.on_complete(nid)
        if (self.table._persister is not None and nid not in self._goals
                and self.graph.consumers.get(nid, 0) > 0):
            # Still needed by a future consumer: a reclaim candidate once durable.
            if len(self._evict_candidates) >= _EVICT_QUEUE_CAP:
                self._evict_candidates.popleft()  # backstop: bound idle-run memory too
            self._evict_candidates.append(nid)
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
        """Refresh the postfix (node counter + smoothed rate + current op).

        The bar's position advances only on goal completion (see
        ``_settle_node``); this call just repaints the postfix so the user sees
        liveness between goals. It never touches ``total`` — that is what kept
        the old node-total bar dancing as the plan expanded.
        """
        elapsed = max(1e-6, time.perf_counter() - self._progress_start)
        rate = self._nodes_done / elapsed
        # `known` = nodes discovered so far (graph.registered_total). It grows
        # monotonically as loops unroll and stops growing once the plan is fully
        # expanded — at which point it IS the true total node count. Shown as a
        # plain counter (done / known), never as a fraction, so it never dances.
        known = self.graph.registered_total
        self._progress.set_description_str(
            f"{self._progress_op} · {self._nodes_done:,}/{known:,} nodes · {rate:,.0f} node/s",
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
            return loaded
        node = self.table.nodes[nid]
        if node.kind == "constant":
            value = node.attrs.get("value")
        elif node.kind == "closure":
            value = None  # closures are trivial; only their captures carry data
        else:
            for child in self.graph.deps(nid):
                self._rematerialize(child)
            self._recomputes += 1  # an evicted value we could neither find nor reload
            value = self.executor._compute(self.table, nid)
        self.table.set_value(nid, value)
        return value

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
                    # persist=False: seq_id is itself a _SEQUENCE_OPERATORS node
                    # and already owns the durable copy; re-persisting the same
                    # value under the loop's id would write it to disk twice and
                    # double-book it in the persist backlog for no reason (the
                    # live tier still records two entries for the same shared
                    # object across the two ids — a conservative overcount in
                    # accounted_bytes, not a real second RAM copy).
                    self._finish(nid, self._rematerialize(seq_id), persist=False)
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
                    self.table.begin(nid)  # enforces the no-double-computation invariant
                    self._kernels_executed += 1
                    started = time.perf_counter()
                    self._in_flight += 1
                    try:
                        value = await self.executor.run(self.table, nid)
                    finally:
                        self._in_flight -= 1
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
            "peak_live_mb": round(self.table.peak_live_bytes / 1024 ** 2, 1),
            "live_budget_mb": round(self.config.max_live_bytes / 1024 ** 2, 1),
            "peak_frontier": self._peak_frontier,
            "loop_window": self.config.loop_window,
            "kernels_executed": self._kernels_executed,
            "recomputes": self._recomputes,
            "expanded_loops": self.admission.expanded_loops,
            "expanded_bodies": self.admission.expanded_bodies,
            "evicted_early": self._evicted_early,
        }
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
