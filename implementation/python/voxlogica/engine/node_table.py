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

from typing import Any

from voxlogica.lazy.hash import hash_node, hash_sequence_item
from voxlogica.lazy.ir import NodeId, NodeSpec
from voxlogica.storage import MaterializationStore, NoCacheStorageBackend, StorageBackend


class DoubleComputationError(RuntimeError):
    """Raised when a node would be computed twice — content addressing forbids it."""


class NodeTable:
    """Hash-consed nodes plus their materialized values across cache tiers."""

    def __init__(self, backend: StorageBackend | None = None):
        self.nodes: dict[NodeId, NodeSpec] = {}
        self.values: dict[NodeId, Any] = {}
        self._running: set[NodeId] = set()
        self.completed: set[NodeId] = set()
        # A disk tier is only worthwhile when there is a real backend to persist
        # to. Without one, the in-RAM ``values`` tier is the whole story, so we
        # skip the store entirely — avoiding a redundant second copy of every
        # value and the cost of serialising results nobody will ever reload.
        # ``memory_capacity=0`` keeps the store a pure disk tier: ``values`` is
        # the only RAM cache, so results are never held in two places at once.
        if backend is not None and not isinstance(backend, NoCacheStorageBackend):
            self._store: MaterializationStore | None = MaterializationStore(backend=backend, memory_capacity=0)
        else:
            self._store = None

    def set_value(self, node_id: NodeId, value: Any) -> None:
        """Place a value in the live tier."""
        self.values[node_id] = value

    def intern(self, node: NodeSpec) -> NodeId:
        """Add a node by structural identity, returning its stable hash id."""
        node_id = hash_node(node)
        self.nodes.setdefault(node_id, node)
        return node_id

    def has_value(self, node_id: NodeId) -> bool:
        """True if the node's value is live in memory."""
        return node_id in self.values

    def persisted(self, node_id: NodeId) -> bool:
        """Cheap existence check against the disk tier (no materialization)."""
        return self._store is not None and self._store.has(node_id)

    def load(self, node_id: NodeId) -> Any:
        """Bring a persisted value back into the live tier, or return None."""
        if self._store is None:
            return None
        value = self._store.get(node_id)
        if value is not None:
            self.set_value(node_id, value)
        return value

    def begin(self, node_id: NodeId) -> None:
        """Mark a node as under computation, enforcing single computation."""
        if node_id in self._running or node_id in self.values:
            raise DoubleComputationError(
                f"node {node_id[:12]} already {'running' if node_id in self._running else 'materialized'}"
            )
        self._running.add(node_id)

    def complete(self, node_id: NodeId, value: Any) -> None:
        """Record a freshly computed value and persist it through the disk tier."""
        self._running.discard(node_id)
        self.set_value(node_id, value)
        self.completed.add(node_id)
        if self._store is not None:
            node = self.nodes[node_id]
            dependencies = list(node.args) + [vid for _, vid in node.kwargs]
            self._store.put(node_id, node.operator, dependencies, value,
                            metadata={"source": "runtime", "operator": node.operator})

    def complete_item(self, node_id: NodeId, index: int, value: Any) -> None:
        """Persist one element of a sequence-valued node under its derived key."""
        if self._store is not None:
            item_id = hash_sequence_item(node_id, index)
            self._store.put(item_id, node_id, [], value, metadata={"source": "runtime", "index": index})

    def evict(self, node_id: NodeId) -> None:
        """Demote a value out of the live tier (recoverable from the backend)."""
        self.values.pop(node_id, None)
        if self._store is not None:
            self._store.forget(node_id)

    def flush(self, timeout_s: float = 10.0) -> None:
        """Block until the persistence tier has drained."""
        if self._store is not None:
            self._store.flush(timeout_s=timeout_s)
