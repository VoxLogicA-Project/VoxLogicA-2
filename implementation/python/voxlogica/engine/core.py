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
from collections import defaultdict
from typing import Any

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
                 backend: StorageBackend | None = None, max_concurrency: int = 0):
        import os
        self.registry = registry or PrimitiveRegistry()
        self.table = NodeTable(backend=backend)
        self.executor = Executor(self.registry, max_concurrency or (os.cpu_count() or 8))
        self.expander = Expander(self.table, self.registry)
        self.max_concurrency = max_concurrency or (os.cpu_count() or 8)

        # Scheduling state (event-loop owned).
        self._pending: dict[NodeId, int] = {}
        self._dependents: dict[NodeId, list[NodeId]] = defaultdict(list)
        self._consumers: dict[NodeId, int] = defaultdict(int)
        self._priority: dict[NodeId, int] = {}
        self._scheduled: set[NodeId] = set()
        self._pinned: set[NodeId] = set()
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
        workers = [asyncio.create_task(self._worker()) for _ in range(self.max_concurrency)]
        try:
            import os
            await self._ready.join()
            if os.environ.get("VOXLOGICA_ENGINE_DEBUG") and any(
                n not in self.table.values for n in self._scheduled
            ):
                self._dump_stuck()
        finally:
            for worker in workers:
                worker.cancel()
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
            if nid in self.table.values or nid in self._scheduled:
                continue
            if nid not in self._goals and self.table.persisted(nid):
                continue  # cached leaf: loaded on demand
            self._scheduled.add(nid)
            discovered.append(nid)
            for dep in self._deps(nid):
                frontier.append(dep)
        self._pin_closures(discovered)
        for nid in discovered:
            if self._register(nid):
                self._enqueue(nid)

    def _register(self, nid: NodeId) -> bool:
        """Wire one node into the dependency graph; return True if ready now."""
        count = 0
        for dep in self._deps(nid):
            self._consumers[dep] += 1
            if dep in self.table.values:
                continue
            if dep in self._scheduled:
                if dep in self.table.completed:
                    self._rematerialize(dep)  # evicted; bring back, do not gate
                    self._pinned.add(dep)
                else:
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
            self.table.values[nid] = value
            self.table.completed.add(nid)
        for dep in self._deps(nid):
            self._release(dep)
        for child in self._dependents.get(nid, ()):
            self._pending[child] -= 1
            if self._pending[child] == 0:
                self._enqueue(child)
        self._settle_node(nid)

    def _release(self, dep: NodeId) -> None:
        """Drop one consumer of dep; demote its value once none remain."""
        remaining = self._consumers.get(dep, 0)
        if remaining <= 0:
            return
        self._consumers[dep] = remaining - 1
        if self._consumers[dep] == 0 and dep not in self._goals and dep not in self._pinned:
            self.table.evict(dep)

    def _expand(self, nid: NodeId, node) -> bool:
        """Splice a loop's per-element bodies into the live schedule."""
        if not self.expander.can_expand(node):
            return False
        try:
            result = self.expander.expand(nid, node)
        except Exception:  # noqa: BLE001 — fall back to the sequential kernel
            result = None
        if result is None:
            return False
        seq_id, new_ids = result
        self._scheduled.update(new_ids)
        self._pin_closures(new_ids)
        priority = self._priority.get(nid, int(Priority.NORMAL))
        for rid in new_ids:
            self._priority[rid] = max(self._priority.get(rid, 0), priority)
            if self._register(rid):
                self._enqueue(rid)
        self._alias[nid] = seq_id
        self._consumers[seq_id] += 1
        if seq_id in self._scheduled and seq_id not in self.table.values:
            self._pending[nid] = 1
            self._dependents[seq_id].append(nid)
        else:
            self._enqueue(nid)
        return True

    def _rematerialize(self, nid: NodeId) -> Any:
        """Recompute a node whose value was evicted but is needed by a new edge."""
        if nid in self.table.values:
            return self.table.values[nid]
        loaded = self.table.load(nid)
        if loaded is not None:
            return loaded
        node = self.table.nodes[nid]
        if node.kind == "constant":
            value = node.attrs.get("value")
        elif node.kind == "closure":
            for child in self._deps(nid):
                self._rematerialize(child)
            value = self._build_closure(node)
        else:
            for child in self._deps(nid):
                self._rematerialize(child)
            value = self.executor._compute(self.table, nid)
        self.table.values[nid] = value
        return value

    # ── Workers ─────────────────────────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Pull ready nodes by priority and drive them to completion."""
        while True:
            _, _, nid = await self._ready.get()
            try:
                if self._first_error is not None or nid in self.table.values:
                    continue
                node = self.table.nodes[nid]
                if nid in self._alias:
                    seq_id = self._alias.pop(nid)
                    self._finish(nid, self.table.values[seq_id])  # forward spliced result
                    self._release(seq_id)                          # the loop was its only consumer
                elif node.kind == "primitive" and self._expand(nid, node):
                    pass  # expanded; the node will run again via its alias
                elif node.kind == "constant":
                    self._finish(nid, node.attrs.get("value"), persist=False)
                elif node.kind == "closure":
                    self._finish(nid, None, persist=False)  # trivial; only its captures matter
                else:
                    for dep in self._deps(nid):
                        if dep not in self.table.values:
                            self._rematerialize(dep)  # recompute deps evicted since gating
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
        """Offer a ready node to the workers at its current priority."""
        entry = (-self._priority.get(nid, 0), next(self._seq), nid)
        if self._ready is None:
            self._pre_ready.append(entry)
        else:
            self._ready.put_nowait(entry)

    def _pin_closures(self, node_ids) -> None:
        """Pin closure captures so a later expansion can re-read them."""
        for cid in node_ids:
            node = self.table.nodes.get(cid)
            if node is not None and node.kind == "closure":
                self._pinned |= Expander.closure_capture_ids(node)

    def _raise_priority(self, nid: NodeId, priority: int) -> None:
        """Propagate a priority bump to a node and its unfinished dependencies."""
        frontier = [nid]
        seen: set[NodeId] = set()
        while frontier:
            current = frontier.pop()
            if current in seen or current in self.table.values:
                continue
            seen.add(current)
            if priority > self._priority.get(current, 0):
                self._priority[current] = priority
                if current in self._scheduled and self._pending.get(current, 0) == 0:
                    self._enqueue(current)  # re-offer at the higher priority
            frontier.extend(self._deps(current))

    def _dump_stuck(self) -> None:
        """Diagnostic: report scheduled nodes that never became ready."""
        import sys
        stuck = [n for n in self._scheduled if n not in self.table.values]
        print(f"[stuck] qsize={self._ready.qsize()} scheduled={len(self._scheduled)} "
              f"values={len(self.table.values)} stuck={len(stuck)} alias={len(self._alias)}", file=sys.stderr)
        for nid in stuck[:12]:
            node = self.table.nodes[nid]
            unmet = [d[:8] for d in self._deps(nid) if d in self._scheduled and d not in self.table.values]
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
