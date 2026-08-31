"""The content-addressed node table: identity, values, and tiered storage.

This is the engine's "computation base". Every expression is a node keyed by its
Merkle hash (see ``voxlogica.lazy.hash``); interning is hash-consed so identical
sub-recipes are one node, shared by every query.

``values`` is the sole in-RAM tier and the working set of live results: the
scheduler drops a value the moment its last consumer has run (see
``DependencyGraph.release``), so the table only ever holds what is still
needed. When a persistent backend is configured, completed values are also
written through to disk, so an evicted value can be reloaded instead of
recomputed; without one (e.g. ``--no-cache``) an evicted value is simply
recomputed on demand.

MEMBERSHIP IS IN-MEMORY: ``persisted()`` answers from an id index loaded once
at startup (a single index-only scan) and appended by the background writer.
The old per-call SQLite probe put one synchronous SELECT on the event loop for
*every node the scheduler discovered* — the classic N+1 — which alone consumed
~30% of the event loop on large runs.

It also enforces the no-double-computation invariant: a node is dispatched at
most once while unmaterialized. Starting a second computation for a hash that is
already running or materialized is a scheduler bug, so ``begin`` raises.
"""

from __future__ import annotations

import os
from typing import Any

from voxlogica.arrays import PolyArray
from voxlogica.handles import iter_handles
from voxlogica.buffer_pool import (buffer_states, pooled_bytes_approx, release_states,
                                   retain_states)
from voxlogica.engine.persist import AsyncPersister, approx_bytes
from voxlogica.lazy.hash import hash_node, hash_sequence_item
from voxlogica.lazy.ir import NodeId, NodeSpec
from voxlogica.storage import NoCacheStorageBackend, StorageBackend, dumps_json


_MISSING = object()

_sitk = None


def _simpleitk():
    global _sitk
    if _sitk is None:
        import SimpleITK
        _sitk = SimpleITK
    return _sitk


def _persist_backlog_budget() -> int:
    """Bytes of unwritten values allowed in flight before dispatch throttles."""
    raw = os.environ.get("VOXLOGICA_PERSIST_BACKLOG_MB")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw) * 1024 * 1024
    return 512 * 1024 * 1024


class DoubleComputationError(RuntimeError):
    """Raised when a node would be computed twice — content addressing forbids it."""


#: Operators the expander unrolls. Duplicated from expander._EXPANDABLE rather
#: than imported, because the table must not depend on the scheduler.
_LOOP_OPERATORS = frozenset({"for_loop", "default.for_loop", "map", "default.map"})


class _LoopWatchingNodes(dict):
    """The node mapping, reporting loop nodes as they are first inserted.

    Nesting is only observable while it is being built: a loop node lands in a
    shared DAG somewhere inside its parent body's cone, never at its root, so
    afterwards nothing distinguishes it from any other node. Watching the
    insertion is the one point that sees it. The overridden __setitem__ costs a
    membership test per node, and fires the callback for a few thousand loops
    out of millions of nodes.
    """

    on_loop: Any = None

    def __setitem__(self, key: Any, value: Any) -> None:
        if key not in self:
            super().__setitem__(key, value)
            if self.on_loop is not None and getattr(value, "operator", None) in _LOOP_OPERATORS:
                self.on_loop(key)
        else:
            super().__setitem__(key, value)


class NodeTable:
    """Hash-consed nodes plus their materialized values and optional disk tier.

    ``values`` is the sole in-RAM tier. When a real backend is configured,
    completed values are also written to disk by a background writer
    (``AsyncPersister``) that never blocks the engine's event loop and frees each
    value as soon as it is written. Under ``--no-cache`` there is no disk tier at
    all — nothing is persisted, so an evicted value is simply recomputed.
    """

    def __init__(self, backend: StorageBackend | None = None):
        # A dict, but one that reports loop nodes as they appear. The reducer
        # writes into this mapping DIRECTLY -- WorkPlan is handed `table.nodes`
        # and inserts through it -- so a hook on intern() never sees the nodes
        # produced while a loop body is being reduced, which is the only moment
        # a loop's enclosing loop is knowable.
        self.nodes: dict[NodeId, NodeSpec] = _LoopWatchingNodes()
        self.values: dict[NodeId, Any] = {}
        self._running: set[NodeId] = set()
        self.completed: set[NodeId] = set()
        self._backend = backend if backend is not None and not isinstance(backend, NoCacheStorageBackend) else None
        # One snapshot query instead of one SELECT per scheduled node. The
        # writer appends as it persists, so the index tracks this run's writes;
        # concurrent *other* processes' writes are missed until the next run —
        # the same staleness window the per-call probe had in practice (and a
        # miss only costs a recompute; content addressing keeps it correct).
        self._persisted_ids: set[NodeId] | None = None
        if self._backend is not None and hasattr(self._backend, "materialized_ids"):
            self._persisted_ids = set(self._backend.materialized_ids())
            if hasattr(self._backend, "set_id_index"):
                self._backend.set_id_index(self._persisted_ids)  # evictions stay truthful
        self._persister = (AsyncPersister(self._backend, _persist_backlog_budget(),
                                          persisted_ids=self._persisted_ids)
                           if self._backend else None)
        # Bytes resident in the live tier, tracked incrementally so the scheduler
        # can bound the working set (admission control) without rescanning values.
        self._sizeof: dict[NodeId, int] = {}
        # Several node ids can intentionally forward the exact same object
        # (notably a dynamic loop id and its spliced sequence id). Count that
        # allocation once while either id remains live; per-node accounting
        # previously double-booked it and applied artificial backpressure.
        self._object_refs: dict[int, int] = {}
        self._object_sizes: dict[int, int] = {}
        self._buffer_leases: dict[NodeId, tuple[Any, ...]] = {}
        # Ids currently handed to the writer, by ANY path (`complete`'s
        # worth-it write or `spill`'s pressure write). A second submit of the
        # same id would put two writer threads on one payload concurrently and
        # can leave a truncated record behind — observed as a zero-length
        # reload ("cannot reshape array of size 0"). One ledger for both paths
        # is what makes "is a write already in flight for this value?"
        # answerable; `evict` drops the entry with the live copy.
        self._write_queued: set[NodeId] = set()
        # Measured kernel wall-time for RESIDENT values only (popped with the
        # value in `evict`, so it is bounded by the live tier, not the plan).
        # Reclaim reads it to choose a value's exit route under pressure:
        # below the worth-it gate -> drop and recompute on demand; above it ->
        # a write is the cheaper exit (see ComputationEngine._reclaim_memory).
        self._compute_ms: dict[NodeId, float] = {}
        # Resident bytes attributed to the operator that produced them, kept
        # incrementally (two dict updates per value, no scan) so "what is
        # holding memory right now" is answerable at any instant without an
        # O(live tier) walk. Sampling that walk was the alternative; at hundreds
        # of thousands of live values it would cost more than the answer is
        # worth, and it would perturb the very run being measured.
        self._resident_by_op: dict[str, int] = {}
        # Tell the disk tier which payloads a live RAM copy is waiting on, so its
        # budget enforcement never evicts the one copy that makes a resident
        # value droppable (see SQLiteResultsDatabase._spilled_ram_copy).
        if self._backend is not None and hasattr(self._backend, "set_spill_guard"):
            self._backend.set_spill_guard(
                lambda node_id: node_id in self.values and node_id in self._write_queued)
        self.live_bytes = 0
        self.peak_live_bytes = 0
        # DAG rows awaiting their batched write. Lineage is METADATA and payload
        # is DATA: they need different retention, so this is recorded for every
        # completed node regardless of whether its value was worth persisting,
        # and never evicted. An evicted value then stays regenerable because the
        # store still holds its recipe.
        self._lineage: list[tuple] = []

    def _retain_object(self, value: Any, size: int) -> int:
        """Retain one reference; return the bytes this call ADDED to the live tier."""
        object_id = id(value)
        refs = self._object_refs.get(object_id, 0)
        added = 0
        if refs == 0:
            self._object_sizes[object_id] = size
            self.live_bytes += size
            added = size
        elif size > self._object_sizes[object_id]:
            added = size - self._object_sizes[object_id]
            self.live_bytes += added
            self._object_sizes[object_id] = size
        self._object_refs[object_id] = refs + 1
        return added

    def _release_object(self, value: Any) -> int:
        """Drop one reference; return the bytes this call FREED from the live tier."""
        object_id = id(value)
        refs = self._object_refs.get(object_id, 0)
        if refs <= 1:
            self._object_refs.pop(object_id, None)
            freed = self._object_sizes.pop(object_id, 0)
            self.live_bytes -= freed
            return freed
        self._object_refs[object_id] = refs - 1
        return 0

    def set_value(self, node_id: NodeId, value: Any) -> int:
        """Place a value in the live tier; return its size (resident bytes)."""
        new_buffer_leases = retain_states(buffer_states(value))
        old_buffer_leases = self._buffer_leases.get(node_id, ())
        previous = self.values.get(node_id, _MISSING)
        if previous is not _MISSING:
            self._account_op(node_id, -self._release_object(previous))
        size = approx_bytes(value)
        self._sizeof[node_id] = size
        self._account_op(node_id, self._retain_object(value, size))
        if self.live_bytes > self.peak_live_bytes:
            self.peak_live_bytes = self.live_bytes
        self.values[node_id] = value
        self._buffer_leases[node_id] = new_buffer_leases
        release_states(old_buffer_leases)
        return size

    def _account_op(self, node_id: NodeId, delta: int) -> None:
        """Attribute a live-tier byte delta to the producing operator."""
        if not delta:
            return
        node = self.nodes.get(node_id)
        operator = getattr(node, "operator", "unknown")
        total = self._resident_by_op.get(operator, 0) + delta
        if total > 0:
            self._resident_by_op[operator] = total
        else:
            self._resident_by_op.pop(operator, None)

    def resident_by_operator(self, top: int = 6) -> list[tuple[str, int]]:
        """The operators holding the live tier right now, largest first."""
        return sorted(self._resident_by_op.items(), key=lambda kv: -kv[1])[:top]

    def intern(self, node: NodeSpec) -> NodeId:
        """Add a node by structural identity, returning its stable hash id."""
        node_id = hash_node(node)
        if node_id not in self.nodes:
            self.nodes[node_id] = node

            # Record the DAG row HERE, not at completion: interning is the one
            # point every node passes through exactly once. Hooking _finish
            # instead lost constants, closures and fusion-completed members --
            # measured on a small for_loop program, 10 of 21 referenced nodes
            # were missing, so the reconstructed DAG had dangling edges.
            self.record_lineage(node_id)
        return node_id

    def has_value(self, node_id: NodeId) -> bool:
        """True if the node's value is live in memory."""
        return node_id in self.values

    @property
    def persist_over_budget(self) -> bool:
        """True while the background writer's unwritten backlog is over budget."""
        return self._persister is not None and self._persister.over_budget

    @property
    def accounted_bytes(self) -> int:
        """True resident total the admission controller must bound: the live
        tier, the unwritten persist backlog, and reusable pooled buffers.

        ``live_bytes`` alone under-reports RSS — a value evicted from the live
        tier stays alive in the persist queue until written, so counting only
        ``live_bytes`` let real memory (and OS RSS) climb far past the budget
        while the engine believed its live tier was small. Folding the backlog
        in here closes that gap and turns a slow disk into real backpressure.
        Pooled buffers are also resident even though immediately reclaimable;
        admission trims them before enforcing its hard ceiling.
        """
        backlog = 0 if self._persister is None else self._persister.pending_bytes
        return self.live_bytes + backlog + pooled_bytes_approx()

    _LINEAGE_BATCH = 512

    def record_lineage(self, node_id: NodeId) -> None:
        """Buffer this node's expression: operator, packed args, kwargs, literals.

        Args are packed as raw 32-byte hashes in argument order — the same bytes
        on every machine, so two stores merge by INSERT OR IGNORE with no id
        remapping. attrs are canonicalized (sorted keys) or two machines would
        hash the same expression differently and the DAG would silently fork.
        """
        if self._persister is None:
            return
        node = self.nodes.get(node_id)
        if node is None:
            return
        try:
            packed = b"".join(bytes.fromhex(a) for a in node.args)
            kwargs = (dumps_json({k: v for k, v in node.normalized_kwargs()})
                      if node.kwargs else None)
            attrs = dumps_json(node.attrs) if node.attrs else None
            self._lineage.append((bytes.fromhex(node_id), node.kind, node.operator,
                                  packed, kwargs, attrs))
        except (ValueError, TypeError):
            return  # a non-hash id (tests use plain strings): nothing to record
        if len(self._lineage) >= self._LINEAGE_BATCH:
            self.flush_lineage()

    def flush_lineage(self) -> None:
        """Hand buffered DAG rows to the writer threads."""
        if self._lineage and self._persister is not None:
            self._persister.submit_lineage(self._lineage)
            self._lineage = []

    def compute_ms_of(self, node_id: NodeId) -> float:
        """Measured kernel cost of a resident value; 0.0 when unknown.

        Unknown covers constants, closures and rematerialized values — all of
        which are cheap to rebuild, so defaulting to 0.0 (evict-and-recompute
        under pressure) is the correct side to land on.
        """
        return self._compute_ms.get(node_id, 0.0)

    def persisted(self, node_id: NodeId) -> bool:
        """Existence check against the disk tier — an in-memory set lookup."""
        if self._persisted_ids is not None:
            return node_id in self._persisted_ids
        return self._backend is not None and self._backend.has(node_id)

    def load(self, node_id: NodeId) -> Any:
        """Bring a persisted value back into the live tier, or return None.

        This is the engine's single live-tier seam: a reloaded image is
        wrapped into a ``PolyArray`` here so every volumetric value the
        engine holds — fresh, reloaded, or later rematerialized — is
        uniformly a ``PolyArray``, matching what a fresh kernel call
        produces (see ``engine/executor.py``). Callers outside the engine
        (serve/inspect) go through ``get_record`` directly and are
        unaffected — this wrapping is scoped to the scheduler's own tier.
        """
        if self._backend is None:
            return None
        record = self._backend.get_record(node_id)
        if record is None or record.value is None:
            return None
        value = record.value
        if not self._references_are_answerable(value):
            # A stored container names its elements by hash. If this run can
            # answer none of those questions the container is not a usable cache
            # hit, however intact its own bytes are.
            return None
        sitk = _simpleitk()
        if sitk is not None and isinstance(value, sitk.Image):
            value = PolyArray.from_sitk(value)
        self.set_value(node_id, value)
        return value

    def _references_are_answerable(self, value: Any) -> bool:
        """Whether every handle inside a loaded value names something reachable.

        Reachable means resident, or present in this run's graph. `persisted()`
        is NOT enough and was tried: it answers from an id index, and a row can
        exist carrying only lineage, so the load then returns None and the
        rebuild dies on a node the graph never interned.

        The cost of reporting a miss here is small and worth being explicit
        about. A warm run that hits the stored container would SKIP the loop
        expansion that defines its elements; refusing the hit makes it expand,
        which interns them, and each element then hits the store on its own. So
        what is recomputed is the list of hashes, and the expensive part -- the
        elements -- is still served from disk.
        """
        for handle in iter_handles(value):
            ref = handle.node
            if ref in self.values or ref in self.nodes:
                continue
            return False
        return True

    def is_claimable(self, node_id: NodeId) -> bool:
        """True iff ``begin(node_id)`` would succeed right now.

        Lets a caller that must claim several nodes together (the fusion
        planner claiming a whole cone, ``engine/fusion.py``) verify every
        member is claimable *before* calling ``begin`` on any of them — so a
        mid-batch ``DoubleComputationError`` can never leave some members
        claimed and others not, and no rollback path is needed.
        """
        return node_id not in self._running and node_id not in self.values

    def begin(self, node_id: NodeId) -> None:
        """Mark a node as under computation, enforcing single computation."""
        if node_id in self._running or node_id in self.values:
            raise DoubleComputationError(
                f"node {node_id[:12]} already {'running' if node_id in self._running else 'materialized'}"
            )
        self._running.add(node_id)

    def complete_without_value(self, node_id: NodeId) -> None:
        """Mark a claimed node completed with no materialized value.

        For a fusion cone's elided interior (``engine/fusion.py``,
        ``DependencyGraph.complete_cone``): the value was computed (in the
        cone's execution scratch, ``Executor._compute_cone``) but
        deliberately never entered ``values`` — every one of its consumers
        is itself a cone member resolving in the same batch, so there is
        nothing to size, persist, or evict. This leaves exactly the two
        invariants any claimed (``begin``-called) node must leave behind:
        the claim is released, and ``node_id in completed`` reads true
        everywhere that checks it (``_schedule_subgraph``'s pruning,
        ``_available``) — a later goal/query sharing this hash-consed id
        must see it as done, not try to re-register or re-dispatch it.

        A later consumer that genuinely needs the value (never in
        ``values``) finds it missing and rematerializes it on demand through
        the existing ``_rematerialize``/dep-check path — the same mechanism
        an ordinary evicted value already uses; no special-casing needed
        there for an elided one.
        """
        self._running.discard(node_id)
        self.completed.add(node_id)

    def complete(self, node_id: NodeId, value: Any, compute_ms: float = 0.0, critical: bool = False,
                 persist: bool = True) -> bool:
        """Record a freshly computed value and hand it to the background writer.

        ``compute_ms`` is the kernel's measured wall-time; it feeds the cache's
        cost-aware eviction so expensive results are kept over cheap ones.

        Persistence is best-effort for cheap values (skipped when the writer is
        behind or when the scheduler judges the value cheaper to recompute than
        to store — ``persist=False``) but *guaranteed* for ``critical`` ones —
        expensive or widely-shared results, exactly what makes cross-run reuse
        pay off. Dropping those was why warm re-runs recomputed everything.

        Returns True iff the value was handed to the writer — i.e. it will
        (barring write failure) become durable, which is what makes it a valid
        proactive-eviction candidate (see ComputationEngine._reclaim_memory).
        """
        self._running.discard(node_id)
        size = self.set_value(node_id, value)
        self.completed.add(node_id)
        self._compute_ms[node_id] = compute_ms
        if self._persister is not None and (critical or (persist and not self._persister.over_budget)):
            node = self.nodes[node_id]
            # Record the in-flight write BEFORE submitting: `spill` consults this
            # to avoid putting a second writer thread on the same payload.
            self._write_queued.add(node_id)
            self._persister.submit(node_id, value, {"source": "runtime", "operator": node.operator},
                                   compute_ms, size=size)
            return True
        return False

    def complete_item(self, node_id: NodeId, index: int, value: Any) -> None:
        """Persist one element of a sequence-valued node under its derived key."""
        if self._persister is not None and not self._persister.over_budget:
            item_id = hash_sequence_item(node_id, index)
            self._persister.submit(item_id, value, {"source": "runtime", "index": index})

    def spill(self, node_id: NodeId) -> bool:
        """Force a resident value onto the writer queue so it can leave RAM.

        Ordinary persistence is *worth-it gated* (``complete``'s ``persist``
        argument): a value cheaper to recompute than to serialize is skipped,
        because writing it would tax dispatch for nothing. That gate answers
        "is this worth CACHING for reuse". Under memory pressure the question
        is a different one — "can this value leave RAM AT ALL" — and the
        cost-based answer is not merely unhelpful there, it is inverted: a
        sub-millisecond kernel over a 35 MB mask is exactly the value we most
        want out of the live tier, and exactly the one the worth-it gate
        refuses to make durable. With eviction requiring durability
        (``ComputationEngine._reclaim_memory``), such a value was permanently
        unreclaimable, so a sweep of cheap large-image kernels could pin tens
        of GB with an empty candidate queue and walk the engine into the OOM
        killer. Measured: a one-case brats021 sweep held 55 GB resident with
        12 MB written and 0-2 eviction candidates.

        Spilling therefore ignores the worth-it gate — but NOT the writer's
        backlog budget. That distinction is load-bearing and was measured the
        hard way: a first version bypassed both, the reclaim sweep submitted
        every candidate it scanned, and the unwritten backlog went 0.5 -> 10.3
        GB in ten seconds while the live copies it was supposed to free stayed
        resident (a value can only be evicted once its write LANDS). Accounted
        bytes then crossed the hard ceiling from the spill itself. A queued
        write is not reclaimed memory; it is the same memory, twice. So when
        the writer is already saturated this returns False: nothing can leave
        RAM right now, and the caller must let the queue drain instead.

        Idempotent — a value already durable or already queued is not
        submitted twice.

        Returns True if the value is durable or is now queued to become so,
        i.e. iff eviction of the live copy is (or will shortly be) safe.
        """
        if self._persister is None or node_id not in self.values:
            return False
        if self.persisted(node_id) or node_id in self._write_queued:
            return True   # already durable, or its write is already in flight
        if self._persister.over_budget:
            return False  # writer saturated: draining it is the only way forward
        value = self.values[node_id]
        node = self.nodes.get(node_id)
        operator = getattr(node, "operator", "unknown")
        self._write_queued.add(node_id)
        self._persister.submit(node_id, value, {"source": "spill", "operator": operator},
                               0.0, size=self._sizeof.get(node_id))
        return True

    def evict(self, node_id: NodeId) -> None:
        """Demote a value out of the live tier.

        A pending disk write keeps its own reference, so the value survives until
        written; the persistent tier can reload it later on demand.
        """
        value = self.values.pop(node_id, _MISSING)
        if value is not _MISSING:
            # Forget the id only once its write has LANDED: from then on
            # `persisted` alone answers "already written", so the ledger stays
            # bounded by the live tier instead of growing once per node for the
            # whole run. While a write is still in flight the entry must stay —
            # `release` can evict a value at any time (last consumer), and a
            # reload before that write lands would otherwise look like a fresh
            # value and let `spill` queue the same payload a second time.
            if self.persisted(node_id):
                self._write_queued.discard(node_id)
            self._sizeof.pop(node_id, None)
            self._compute_ms.pop(node_id, None)
            self._account_op(node_id, -self._release_object(value))
            release_states(self._buffer_leases.pop(node_id, ()))

    def flush(self, timeout_s: float = 600.0) -> None:
        self.flush_lineage()
        """Block until the background writer has drained (called once, at end of run).

        Must actually finish: the run promised to persist its critical results
        (the reuse cut), and those complete last, so a short timeout would abandon
        exactly them — leaving a warm re-run nothing to prune. The critical set is
        small, so this drains quickly.
        """
        if self._persister is not None:
            self._persister.flush(timeout_s=timeout_s)
