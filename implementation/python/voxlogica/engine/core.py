"""The live computation engine.

Ties the pieces together: a query is submitted (a goal node to materialize), its
unmaterialized subgraph is registered with the scheduler, and a pool of workers
drains a priority-ordered ready queue — running primitives on the executor,
expanding loops into nodes via the single reduction semantics, releasing and
demoting values once their last consumer has run. Queries sharing subexpressions
share work automatically (Merkle identity); a higher-priority query lifts its
dependencies above older work.

Coordination runs on one event loop (single-writer over the scheduling maps, no
locks); only primitive kernels run off-thread.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import os
import sys
import time
from collections import defaultdict
from typing import Any

from tqdm import tqdm


def _default_live_budget() -> int:
    """Default cap on live-tier bytes for admission control.

    Bounds the working set so a wide fan-out cannot make the whole DAG live at
    once. Overridable via ``VOXLOGICA_MAX_LIVE_GB``; otherwise ~40% of system RAM
    (fallback 8 GB), which keeps the frontier well within memory while leaving
    room for the interpreter and off-heap kernel buffers.
    """
    raw = os.environ.get("VOXLOGICA_MAX_LIVE_GB")
    if raw:
        try:
            return max(1, int(float(raw) * 1024 ** 3))
        except ValueError:
            pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return int(total * 0.4)
    except (ValueError, OSError, AttributeError):
        return 8 * 1024 ** 3

from voxlogica.engine.executor import Executor
from voxlogica.engine.expander import Expander
from voxlogica.engine.node_table import NodeTable
from voxlogica.engine.priority import Priority
from voxlogica.engine.query import Query, QueryStatus
from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import StorageBackend

_SEQUENCE_OPERATORS = {"default.sequence", "sequence", "default.map", "map",
                       "default.for_loop", "for_loop", "default.filter", "filter"}


class ComputationEngine:
    """A persistent, content-addressed, priority-scheduled evaluator."""

    def __init__(self, registry: PrimitiveRegistry | None = None,
                 backend: StorageBackend | None = None, max_concurrency: int = 0,
                 progress: bool = False, debug: bool = False, max_live_bytes: int = 0):
        self.registry = registry or PrimitiveRegistry()
        self.table = NodeTable(backend=backend)
        self.max_concurrency = max_concurrency or (os.cpu_count() or 8)
        self._max_live_bytes = max_live_bytes or _default_live_budget()
        self.executor = Executor(self.registry, self.max_concurrency)
        self.expander = Expander(self.table, self.registry)
        self._show_progress = progress
        self._debug = debug
        self._progress: tqdm | None = None

        # Scheduling state (event-loop owned).
        self._pending: dict[NodeId, int] = {}
        self._dependents: dict[NodeId, list[NodeId]] = defaultdict(list)
        self._consumers: dict[NodeId, int] = defaultdict(int)
        self._priority: dict[NodeId, int] = {}
        self._scheduled: set[NodeId] = set()
        self._goals: set[NodeId] = set()
        self._alias: dict[NodeId, NodeId] = {}
        self._deps_cache: dict[NodeId, frozenset[NodeId]] = {}
        self._waiters: dict[NodeId, list[Query]] = defaultdict(list)
        # The ready queue is created once the event loop is running; submissions
        # made before run() buffer here and are seeded in run().
        self._ready: asyncio.PriorityQueue | None = None
        self._pre_ready: list[tuple[int, int, NodeId]] = []
        self._seq = itertools.count()
        self._queries: list[Query] = []
        self._query_ids = itertools.count()
        self._first_error: BaseException | None = None
        # Admission control: ready nodes held back while the live tier is over
        # budget, so a wide fan-out cannot make the whole DAG live at once.
        self._deferred: list[tuple[int, int, NodeId]] = []
        self._recomputes = 0  # times an evicted value had to be recomputed (not reloaded)
        self._live_refresh = 0  # throttles the O(n) live-set push to the backend
        # Bounded loop unrolling: a loop registers only a window of its
        # independent bodies at a time and admits the next as they complete, so a
        # 30x150 sweep never makes the whole DAG "live" at once (which is what
        # left the cache no dead values to evict and forced live eviction/thrash).
        self._body_window = max(self.max_concurrency, int(os.environ.get("VOXLOGICA_LOOP_WINDOW", 0)) or self.max_concurrency)
        self._loop_bodies: dict[NodeId, list[NodeId]] = {}
        self._loop_next: dict[NodeId, int] = {}
        self._body_owner: dict[NodeId, NodeId] = {}
        self._peak_frontier = 0  # max scheduled-but-not-completed nodes (in-flight breadth)

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
        """Drain the ready queue until every scheduled node is materialized."""
        self._ready = asyncio.PriorityQueue()
        for entry in self._pre_ready:
            self._ready.put_nowait(entry)
        self._pre_ready.clear()
        if self._show_progress:
            # disable=None auto-disables the bar when stderr is not a TTY
            # (redirected to a file/pipe), keeping logs clean; dynamic_ncols
            # re-reads the terminal width on every refresh so the bar reflows
            # instead of garbling on resize.
            self._progress = tqdm(total=len(self._scheduled), desc="nodes", unit="node",
                                  dynamic_ncols=True, disable=None, file=sys.stderr, leave=True)
        workers = [asyncio.create_task(self._worker()) for _ in range(self.max_concurrency)]
        try:
            await self._ready.join()
            if self._debug and any(n not in self.table.completed for n in self._scheduled):
                self._dump_stuck()
        finally:
            for worker in workers:
                worker.cancel()
            if self._progress is not None:
                self._progress.close()
                self._progress = None
        self.table.flush()
        if self._first_error is not None:
            raise self._first_error

    # ── Scheduling ──────────────────────────────────────────────────────────────────────────

    def _schedule_subgraph(self, goal: NodeId, priority: int) -> None:
        """BFS from a goal, pruning at materialized/persisted nodes, registering the rest."""
        frontier = [goal]
        seen: set[NodeId] = set()
        discovered: list[NodeId] = []
        while frontier:
            nid = frontier.pop()
            if nid in seen:
                continue
            seen.add(nid)
            self._priority[nid] = max(self._priority.get(nid, 0), priority)
            if nid in self._scheduled or nid in self.table.completed:
                continue
            if nid not in self._goals and self.table.persisted(nid):
                continue  # cached leaf: loaded on demand
            self._scheduled.add(nid)
            discovered.append(nid)
            for dep in self._deps(nid):
                frontier.append(dep)
        for nid in discovered:
            if self._register(nid):
                self._enqueue(nid)

    def _register(self, nid: NodeId) -> bool:
        """Wire one node into the dependency graph; return True if ready now.

        Readiness is gated only on whether a dependency has *completed* (a
        monotonic fact), never on whether its value is currently resident.
        Values may be evicted under memory pressure and rematerialised on demand,
        so gating on residency would risk waiting forever on an evicted value.
        """
        count = 0
        for dep in self._deps(nid):
            self._consumers[dep] += 1
            if dep in self._scheduled and dep not in self.table.completed:
                count += 1
                self._dependents[dep].append(nid)
        self._pending[nid] = count
        return count == 0

    def _finish(self, nid: NodeId, value: Any, persist: bool = True, compute_ms: float = 0.0) -> None:
        """Record a value, release dependencies, and unblock dependents.

        Constants and closures are trivial and not persisted: a closure exists
        only to force its captures to materialize and to gate its loop; the loop
        reads the closure's structure, never a computed closure value.
        """
        node = self.table.nodes[nid]
        if persist:
            self.table.complete(nid, value, compute_ms)
            if node.operator in _SEQUENCE_OPERATORS:
                for index, item in enumerate(value):
                    self.table.complete_item(nid, index, item)
        else:
            self.table.set_value(nid, value)
            self.table.completed.add(nid)
        if node.kind != "closure":
            # A closure completes trivially, but its captures must stay pinned
            # until the loop it gates has expanded — the per-element bodies read
            # those captures. Releasing here would evict a value (e.g. the loop's
            # input image) that the imminent expansion still needs, forcing a
            # wasteful recompute. _expand transfers the hold to the bodies.
            for dep in self._deps(nid):
                self._release(dep)
        for child in self._dependents.get(nid, ()):
            self._pending[child] -= 1
            if self._pending[child] == 0:
                self._enqueue(child)
        # If this node was a loop body, admit the next body of its loop now that
        # one slot has freed — keeping ~_body_window bodies in flight.
        owner = self._body_owner.pop(nid, None)
        if owner is not None:
            self._admit_bodies(owner, 1)
        frontier = len(self._scheduled) - len(self.table.completed)
        if frontier > self._peak_frontier:
            self._peak_frontier = frontier
        # Refresh storage's live-node set so eviction prefers dead values, but
        # only periodically: recomputing it is O(scheduled), and disk-evicting a
        # still-RAM-resident value is free anyway, so a slightly stale set is
        # harmless. (Doing this every completion was quadratic and starved the
        # event loop — the original cause of the single-core collapse.)
        if self.table._backend is not None:
            self._live_refresh += 1
            if self._live_refresh >= 128:
                self._live_refresh = 0
                self.table._backend.set_live_nodes(self._compute_live_values())
        self._settle_node(nid)
        if self._progress is not None:
            self._progress.set_postfix_str(node.operator, refresh=False)
            self._progress.update(1)

    def _release(self, dep: NodeId) -> None:
        """Drop a dependency's value once its last consumer has run.

        Readiness is gated on completion, never on residency, so freeing the
        value here at worst costs a recompute if a later query demands it again
        — the value never survives past its last consumer, matching the lazy
        strategy's garbage-collection behaviour and keeping the live tier small.
        """
        remaining = self._consumers.get(dep, 0)
        if remaining <= 0:
            return
        remaining -= 1
        self._consumers[dep] = remaining
        if remaining == 0 and dep not in self._goals:
            self.table.evict(dep)

    def _expand(self, nid: NodeId, node) -> None:
        """Splice a loop's per-element bodies into the live schedule.

        The spliced subgraph is scheduled through the very same routine as any
        other subgraph (``_schedule_subgraph``), so an expanded node is treated
        identically to one present before expansion: if it is already persisted
        it is pruned and loaded from the cache on demand rather than recomputed.
        There is deliberately one scheduling/caching path, never a separate one
        for expanded work — generating the nodes at all is precisely what lets
        the cache be hit.
        """
        self._rematerialize(node.args[0])  # ensure the iterable value is resident
        result = self.expander.expand(nid, node)
        if result is None:
            raise RuntimeError(f"cannot expand loop node {nid[:12]} ({node.operator})")
        seq_id, _new_ids = result
        priority = self._priority.get(nid, int(Priority.NORMAL))
        if (seq_id in self._scheduled or seq_id in self.table.completed
                or (seq_id not in self._goals and self.table.persisted(seq_id))):
            # Already scheduled or cached: nothing to unroll, take the normal path.
            self._schedule_subgraph(seq_id, priority)
        else:
            # Bounded unroll: register the sequence node depending on all bodies,
            # but only schedule a window of body subtrees now; the rest are
            # admitted as bodies complete (see _finish -> _admit_bodies). This
            # keeps the set of incomplete (== "live") nodes small so the cache
            # always has dead values to evict instead of live ones.
            self._scheduled.add(seq_id)
            self._priority[seq_id] = max(self._priority.get(seq_id, 0), priority)
            pending = []
            count = 0
            for body in self.table.nodes[seq_id].args:
                self._consumers[body] += 1
                if body in self.table.completed:
                    continue
                self._dependents[body].append(seq_id)
                self._body_owner[body] = seq_id
                pending.append(body)
                count += 1
            self._pending[seq_id] = count
            if count == 0:
                self._enqueue(seq_id)
            else:
                self._loop_bodies[seq_id] = pending
                self._loop_next[seq_id] = 0
                self._admit_bodies(seq_id, self._body_window)
        # The bodies just registered as the real consumers of the closure's
        # captures; hand the closure's pin over to them (see _finish). The
        # captures stay resident throughout — never dropping to zero — so the
        # loop's inputs are read once, not recomputed per expansion.
        for dep in self._deps(node.args[1]):
            self._release(dep)
        self._alias[nid] = seq_id
        self._consumers[seq_id] += 1
        if seq_id in self._scheduled and seq_id not in self.table.completed:
            self._pending[nid] = 1
            self._dependents[seq_id].append(nid)
        else:
            self._enqueue(nid)

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
            for child in self._deps(nid):
                self._rematerialize(child)
            self._recomputes += 1  # an evicted value we could neither find nor reload
            value = self.executor._compute(self.table, nid)
        self.table.set_value(nid, value)
        return value

    # ── Workers ─────────────────────────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Pull ready nodes by priority and drive them to completion."""
        while True:
            _, _, nid = await self._ready.get()
            try:
                if self._first_error is not None or nid in self.table.completed:
                    continue  # cancelled, or a duplicate of an already-finished node
                node = self.table.nodes[nid]
                if nid in self._alias:
                    seq_id = self._alias.pop(nid)
                    self._finish(nid, self._rematerialize(seq_id))  # forward spliced result
                    self._release(seq_id)                            # the loop was its only consumer
                elif self.expander.can_expand(node):
                    self._expand(nid, node)  # splices bodies; node re-runs via its alias
                elif node.kind == "constant":
                    self._finish(nid, node.attrs.get("value"), persist=False)
                elif node.kind == "closure":
                    self._finish(nid, None, persist=False)  # trivial; only its captures matter
                else:
                    # Throttle *new* kernels when the disk writer is behind, so
                    # the unwritten backlog can't grow without bound. This yields
                    # the event loop (completions and writes keep flowing) rather
                    # than blocking it, then re-queues this node to retry.
                    if self.table.persist_over_budget:
                        await asyncio.sleep(0.002)
                        self._enqueue(nid)
                        continue
                    for dep in self._deps(nid):
                        if dep not in self.table.values:
                            self._rematerialize(dep)  # recompute deps evicted under pressure
                    self.table.begin(nid)  # enforces the no-double-computation invariant
                    started = time.perf_counter()
                    value = await self.executor.run(self.table, nid)
                    # measured recompute cost feeds the cache's cost-aware eviction
                    self._finish(nid, value, compute_ms=(time.perf_counter() - started) * 1000.0)
            except Exception as exc:  # noqa: BLE001
                if self._first_error is None:
                    self._first_error = exc
                self._fail_waiters(nid, exc)
            finally:
                # Admit held-back work before signalling this task done, so the
                # ready queue is never observed empty while nodes are deferred
                # (which would let _ready.join() return with work outstanding).
                self._admit()
                self._ready.task_done()

    # ── Helpers ─────────────────────────────────────────────────────────────────────────────

    def _deps(self, nid: NodeId) -> frozenset[NodeId]:
        """All dependency ids of a node, including closure-capture references.

        Node specs are immutable, so a node's dependency set is stable: compute
        it once and memoise. This hot helper is called several times per node
        (registration, completion, expansion, priority raising), and rebuilding
        the set each time was pure scheduling overhead.
        """
        cached = self._deps_cache.get(nid)
        if cached is None:
            cached = frozenset(Expander.dependencies(self.table.nodes[nid]))
            self._deps_cache[nid] = cached
        return cached

    def metrics(self) -> dict[str, Any]:
        """Scheduler/cache statistics for the run summary.

        ``recomputes`` should be ~0: a healthy run computes each node once, so a
        large value signals eviction⇄recompute thrash. ``peak_live_bytes`` is the
        high-water mark of the resident working set (what admission control bounds).
        """
        m: dict[str, Any] = {
            "peak_live_mb": round(self.table.peak_live_bytes / 1024 ** 2, 1),
            "live_budget_mb": round(self._max_live_bytes / 1024 ** 2, 1),
            "peak_frontier": self._peak_frontier,
            "loop_window": self._body_window,
            "recomputes": self._recomputes,
        }
        backend = self.table._backend
        if backend is not None and hasattr(backend, "stats"):
            s = backend.stats()
            m["cache_bytes_mb"] = round(s.get("payload_bytes", 0) / 1024 ** 2, 1)
            m["evicted_dead"] = s.get("evicted_dead", 0)
            m["evicted_live"] = s.get("evicted_live", 0)
        return m

    def _compute_live_values(self) -> set[NodeId]:
        """Return nodes still needed by any active goal or incomplete work.

        A value is "live" if there is any path from it to an active goal or any
        node that is queued, running, or not yet completed. The storage backend
        uses this to prioritize what to evict: dead values first, live only if
        forced. This prevents evicting something that will be needed soon.
        """
        incomplete = set(nid for nid in self._scheduled if nid not in self.table.completed)
        for query in self._queries:
            if query.node_id not in self.table.completed:
                incomplete.add(query.node_id)
        live = set()
        frontier = list(incomplete)
        while frontier:
            nid = frontier.pop()
            if nid in live:
                continue
            live.add(nid)
            frontier.extend(self._deps(nid))
        return live

    def _enqueue(self, nid: NodeId) -> None:
        """Offer a ready node to the workers at its current priority.

        Equal-priority nodes are drained newest-first (LIFO, via the negated
        sequence number). This makes evaluation depth-first: a freshly produced
        value is consumed by its dependent before more siblings are produced, so
        intermediates (e.g. a threshold image feeding a single ``volume``) are
        evicted almost immediately instead of piling up breadth-first. This is
        what keeps the live tier — and peak memory — small under wide fan-out.
        """
        entry = (-self._priority.get(nid, 0), -next(self._seq), nid)
        if self._ready is None:
            self._pre_ready.append(entry)
        elif (self.table.live_bytes > self._max_live_bytes
              and self._ready.qsize() >= self.max_concurrency):
            # Live tier over budget and workers already have enough to do: hold
            # this node back rather than widening the frontier. It is admitted
            # again once completions free live bytes (or the queue would starve).
            heapq.heappush(self._deferred, entry)
        else:
            self._ready.put_nowait(entry)

    def _admit_bodies(self, seq_id: NodeId, count: int) -> None:
        """Schedule up to ``count`` more of a loop's not-yet-admitted bodies.

        Called with the window size at expansion, then with 1 each time a body of
        this loop completes — so roughly ``_body_window`` bodies are in flight at
        once, bounding how much of the DAG is live regardless of the loop's width.
        """
        bodies = self._loop_bodies.get(seq_id)
        if bodies is None:
            return
        index = self._loop_next[seq_id]
        priority = self._priority.get(seq_id, int(Priority.NORMAL))
        admitted = 0
        while index < len(bodies) and admitted < count:
            body = bodies[index]
            index += 1
            admitted += 1
            if body not in self._scheduled and body not in self.table.completed:
                self._schedule_subgraph(body, priority)
        self._loop_next[seq_id] = index
        if index >= len(bodies):  # fully unrolled; drop the bookkeeping
            self._loop_bodies.pop(seq_id, None)
            self._loop_next.pop(seq_id, None)

    def _admit(self) -> None:
        """Move held-back nodes into the ready queue as capacity frees up.

        Admits while the live tier is under budget, and *always* admits when the
        ready queue would otherwise starve workers — this is the progress floor
        that guarantees forward progress (and prevents ``_ready.join()`` from
        returning while work is still deferred), at the cost of briefly exceeding
        the soft budget when a single step's inputs are unavoidably large.
        """
        if self._ready is None:
            return
        while self._deferred:
            starving = self._ready.qsize() < self.max_concurrency
            if self.table.live_bytes > self._max_live_bytes and not starving:
                break
            self._ready.put_nowait(heapq.heappop(self._deferred))

    def _raise_priority(self, nid: NodeId, priority: int) -> None:
        """Propagate a priority bump to a node and its unfinished dependencies.

        Each node is enqueued exactly once, so we do not re-offer already-queued
        nodes; raising the recorded priority lifts not-yet-enqueued descendants
        when they become ready.
        """
        frontier = [nid]
        seen: set[NodeId] = set()
        while frontier:
            current = frontier.pop()
            if current in seen or current in self.table.completed:
                continue
            seen.add(current)
            self._priority[current] = max(self._priority.get(current, 0), priority)
            frontier.extend(self._deps(current))

    def _dump_stuck(self) -> None:
        """Diagnostic: report scheduled nodes that never completed."""
        import sys
        stuck = [n for n in self._scheduled if n not in self.table.completed]
        print(f"[stuck] qsize={self._ready.qsize()} scheduled={len(self._scheduled)} "
              f"completed={len(self.table.completed)} stuck={len(stuck)} alias={len(self._alias)}", file=sys.stderr)
        for nid in stuck[:12]:
            node = self.table.nodes[nid]
            unmet = [d[:8] for d in self._deps(nid) if d in self._scheduled and d not in self.table.completed]
            print(f"  {nid[:8]} op={node.operator} kind={node.kind} pending={self._pending.get(nid)} "
                  f"alias={nid in self._alias} unmet={unmet}", file=sys.stderr)

    def _settle_node(self, nid: NodeId) -> None:
        """Resolve any queries whose goal node just materialized."""
        for query in self._waiters.get(nid, ()):
            query._settle(QueryStatus.DONE, value=self.table.values.get(nid))

    def _fail_waiters(self, nid: NodeId, error: BaseException) -> None:
        """Mark queries on a failed node as failed."""
        for query in self._waiters.get(nid, ()):
            query._settle(QueryStatus.FAILED, error=error)
