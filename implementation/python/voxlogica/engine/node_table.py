"""The content-addressed node table: identity, values, and tiered storage.

This is the engine's "computation base". Every expression is a node keyed by its
Merkle hash (see ``voxlogica.lazy.hash``); interning is hash-consed so identical
sub-recipes are one node, shared by every query.

``values`` is the sole in-RAM tier and the working set of live results: the
scheduler drops a value the moment its last consumer has run (see
``ComputationEngine._release``), so the table only ever holds what is still
needed. When a persistent backend is configured, completed values are also
written through to disk, so an evicted value can be reloaded instead of
recomputed; without one (e.g. ``--no-cache``) an evicted value is simply
recomputed on demand.

It also enforces the no-double-computation invariant: a node is dispatched at
most once while unmaterialized. Starting a second computation for a hash that is
already running or materialized is a scheduler bug, so ``begin`` raises.
"""

from __future__ import annotations

import os
from typing import Any

from voxlogica.engine.persist import AsyncPersister, approx_bytes
from voxlogica.lazy.hash import hash_node, hash_sequence_item
from voxlogica.lazy.ir import NodeId, NodeSpec
from voxlogica.storage import NoCacheStorageBackend, StorageBackend


def _persist_backlog_budget() -> int:
    """Bytes of unwritten values allowed in flight before dispatch throttles."""
    raw = os.environ.get("VOXLOGICA_PERSIST_BACKLOG_MB")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw) * 1024 * 1024
    return 512 * 1024 * 1024


class DoubleComputationError(RuntimeError):
    """Raised when a node would be computed twice — content addressing forbids it."""


class NodeTable:
    """Hash-consed nodes plus their materialized values and optional disk tier.

    ``values`` is the sole in-RAM tier. When a real backend is configured,
    completed values are also written to disk by a background writer
    (``AsyncPersister``) that never blocks the engine's event loop and frees each
    value as soon as it is written. Under ``--no-cache`` there is no disk tier at
    all — nothing is persisted, so an evicted value is simply recomputed.
    """

    def __init__(self, backend: StorageBackend | None = None):
        self.nodes: dict[NodeId, NodeSpec] = {}
        self.values: dict[NodeId, Any] = {}
        self._running: set[NodeId] = set()
        self.completed: set[NodeId] = set()
        self._backend = backend if backend is not None and not isinstance(backend, NoCacheStorageBackend) else None
        self._persister = AsyncPersister(self._backend, _persist_backlog_budget()) if self._backend else None
        # Bytes resident in the live tier, tracked incrementally so the scheduler
        # can bound the working set (admission control) without rescanning values.
        self._sizeof: dict[NodeId, int] = {}
        self.live_bytes = 0
        self.peak_live_bytes = 0

    def set_value(self, node_id: NodeId, value: Any) -> None:
        """Place a value in the live tier, updating the resident-bytes total."""
        if node_id in self._sizeof:
            self.live_bytes -= self._sizeof[node_id]
        size = approx_bytes(value)
        self._sizeof[node_id] = size
        self.live_bytes += size
        if self.live_bytes > self.peak_live_bytes:
            self.peak_live_bytes = self.live_bytes
        self.values[node_id] = value

    def intern(self, node: NodeSpec) -> NodeId:
        """Add a node by structural identity, returning its stable hash id."""
        node_id = hash_node(node)
        self.nodes.setdefault(node_id, node)
        return node_id

    def has_value(self, node_id: NodeId) -> bool:
        """True if the node's value is live in memory."""
        return node_id in self.values

    @property
    def persist_over_budget(self) -> bool:
        """True while the background writer's unwritten backlog is over budget."""
        return self._persister is not None and self._persister.over_budget

    def persisted(self, node_id: NodeId) -> bool:
        """Cheap existence check against the disk tier (no materialization)."""
        return self._backend is not None and self._backend.has(node_id)

    def load(self, node_id: NodeId) -> Any:
        """Bring a persisted value back into the live tier, or return None."""
        if self._backend is None:
            return None
        record = self._backend.get_record(node_id)
        if record is None or record.value is None:
            return None
        self.set_value(node_id, record.value)
        return record.value

    def begin(self, node_id: NodeId) -> None:
        """Mark a node as under computation, enforcing single computation."""
        if node_id in self._running or node_id in self.values:
            raise DoubleComputationError(
                f"node {node_id[:12]} already {'running' if node_id in self._running else 'materialized'}"
            )
        self._running.add(node_id)

    def complete(self, node_id: NodeId, value: Any, compute_ms: float = 0.0) -> None:
        """Record a freshly computed value and hand it to the background writer.

        ``compute_ms`` is the kernel's measured wall-time; it feeds the cache's
        cost-aware eviction so expensive results are kept over cheap ones.
        """
        self._running.discard(node_id)
        self.set_value(node_id, value)
        self.completed.add(node_id)
        # Caching is best-effort: when the background writer is behind (backlog
        # over budget) we skip persisting this value rather than stalling compute.
        # The value stays in the live tier for correctness; it just may not be on
        # disk for later reuse. Compute never waits on the cache.
        if self._persister is not None and not self._persister.over_budget:
            node = self.nodes[node_id]
            self._persister.submit(node_id, value, {"source": "runtime", "operator": node.operator}, compute_ms)

    def complete_item(self, node_id: NodeId, index: int, value: Any) -> None:
        """Persist one element of a sequence-valued node under its derived key."""
        if self._persister is not None and not self._persister.over_budget:
            item_id = hash_sequence_item(node_id, index)
            self._persister.submit(item_id, value, {"source": "runtime", "index": index})

    def evict(self, node_id: NodeId) -> None:
        """Demote a value out of the live tier.

        A pending disk write keeps its own reference, so the value survives until
        written; the persistent tier can reload it later on demand.
        """
        if self.values.pop(node_id, None) is not None:
            self.live_bytes -= self._sizeof.pop(node_id, 0)

    def flush(self, timeout_s: float = 30.0) -> None:
        """Block until the background writer has drained (called once, at end of run)."""
        if self._persister is not None:
            self._persister.flush(timeout_s=timeout_s)
