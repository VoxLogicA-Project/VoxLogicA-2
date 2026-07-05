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
import itertools
import os
import sys
from collections import defaultdict
from typing import Any

from tqdm import tqdm

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
                 progress: bool = False, debug: bool = False):
        self.registry = registry or PrimitiveRegistry()
        self.table = NodeTable(backend=backend)
        self.max_concurrency = max_concurrency or (os.cpu_count() or 8)
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
        self._waiters: dict[NodeId, list[Query]] = defaultdict(list)
        # The ready queue is created once the event loop is running; submissions
        # made before run() buffer here and are seeded in run().
        self._ready: asyncio.PriorityQueue | None = None
        self._pre_ready: list[tuple[int, int, NodeId]] = []
        self._seq = itertools.count()
        self._queries: list[Query] = []
        self._query_ids = itertools.count()
        self._first_error: BaseException | None = None

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
            self._progress = tqdm(total=len(self._scheduled), desc="nodes", unit="node",
                                  dynamic_ncols=True, file=sys.stderr, leave=True)
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

    def _finish(self, nid: NodeId, value: Any, persist: bool = True) -> None:
        """Record a value, release dependencies, and unblock dependents.

        Constants and closures are trivial and not persisted: a closure exists
        only to force its captures to materialize and to gate its loop; the loop
        reads the closure's structure, never a computed closure value.
        """
        node = self.table.nodes[nid]
        if persist:
            self.table.complete(nid, value)
            if node.operator in _SEQUENCE_OPERATORS:
                for index, item in enumerate(value):
                    self.table.complete_item(nid, index, item)
        else:
            self.table.set_value(nid, value)
            self.table.completed.add(nid)
        for dep in self._deps(nid):
            self._release(dep)
        for child in self._dependents.get(nid, ()):
            self._pending[child] -= 1
            if self._pending[child] == 0:
                self._enqueue(child)
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

        The iterable is rematerialised first, so expansion always succeeds; the
        loop node then aliases the spliced sequence and forwards its value.
        """
        self._rematerialize(node.args[0])  # ensure the iterable value is resident
        result = self.expander.expand(nid, node)
        if result is None:
            raise RuntimeError(f"cannot expand loop node {nid[:12]} ({node.operator})")
        seq_id, new_ids = result
        self._scheduled.update(new_ids)
        if self._progress is not None and new_ids:
            self._progress.total += len(new_ids)  # the graph grew; track the new work
            self._progress.refresh()
        priority = self._priority.get(nid, int(Priority.NORMAL))
        for rid in new_ids:
            self._priority[rid] = max(self._priority.get(rid, 0), priority)
            if self._register(rid):
                self._enqueue(rid)
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
                    for dep in self._deps(nid):
                        if dep not in self.table.values:
                            self._rematerialize(dep)  # recompute deps evicted under pressure
                    self.table.begin(nid)  # enforces the no-double-computation invariant
                    value = await self.executor.run(self.table, nid)
                    self._finish(nid, value)
            except Exception as exc:  # noqa: BLE001
                if self._first_error is None:
                    self._first_error = exc
                self._fail_waiters(nid, exc)
            finally:
                self._ready.task_done()

    # ── Helpers ─────────────────────────────────────────────────────────────────────────────

    def _deps(self, nid: NodeId) -> set[NodeId]:
        """All dependency ids of a node, including closure-capture references."""
        return Expander.dependencies(self.table.nodes[nid])

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
        else:
            self._ready.put_nowait(entry)

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
