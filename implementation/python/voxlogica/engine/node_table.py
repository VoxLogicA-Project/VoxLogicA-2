"""The content-addressed node table: identity, values, and tiered storage.

This is the engine's "computation base". Every expression is a node keyed by its
Merkle hash (see ``voxlogica.lazy.hash``); interning is hash-consed so identical
sub-recipes are one node, shared by every query. Live values sit in an in-memory
tier; evicted values are recomputable from the recipe and may be reloaded from
the persistent backend.

It also enforces the no-double-computation invariant: a node is dispatched at
most once while unmaterialized. Starting a second computation for a hash that is
already running or materialized is a scheduler bug, so ``begin`` raises.
"""

from __future__ import annotations

from typing import Any

from voxlogica.engine.memory import estimate_bytes
from voxlogica.lazy.hash import hash_node, hash_sequence_item
from voxlogica.lazy.ir import NodeId, NodeSpec
from voxlogica.storage import MaterializationStore, StorageBackend


class DoubleComputationError(RuntimeError):
    """Raised when a node would be computed twice — content addressing forbids it."""


class NodeTable:
    """Hash-consed nodes plus their materialized values across cache tiers."""

    def __init__(self, backend: StorageBackend | None = None):
        self.nodes: dict[NodeId, NodeSpec] = {}
        self.values: dict[NodeId, Any] = {}
        self.live_bytes = 0  # estimated resident size of self.values
        self._store = MaterializationStore(backend=backend, read_through=True, write_through=True)
        self._backend = backend
        self._running: set[NodeId] = set()
        self.completed: set[NodeId] = set()

    def set_value(self, node_id: NodeId, value: Any) -> None:
        """Place a value in the live tier, accounting for its size once."""
        if node_id not in self.values:
            self.live_bytes += estimate_bytes(value)
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
        """Cheap existence check against the persistent tier (no materialization)."""
        if node_id in self._store._memory:
            return True
        return self._backend is not None and self._backend.has(node_id)

    def load(self, node_id: NodeId) -> Any:
        """Bring a persisted value back into the live tier, or return None."""
        value = self._store.get(node_id)
        if value is None and self._backend is not None:
            record = self._backend.get_record(node_id)
            if record is not None:
                value = record.payload_bin if record.vox_type == "image" else record.payload_json["value"]
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
        """Record a freshly computed value and persist it through the tiers."""
        self._running.discard(node_id)
        self.set_value(node_id, value)
        self.completed.add(node_id)
        node = self.nodes[node_id]
        dependencies = list(node.args) + [vid for _, vid in node.kwargs]
        self._store.put(node_id, node.operator, dependencies, value,
                        metadata={"source": "runtime", "operator": node.operator})
        if self._backend is not None:
            self._backend.put_success(node_id, value, metadata={"source": "runtime", "operator": node.operator})

    def complete_item(self, node_id: NodeId, index: int, value: Any) -> None:
        """Persist one element of a sequence-valued node under its derived key."""
        item_id = hash_sequence_item(node_id, index)
        self._store.put(item_id, node_id, [], value, metadata={"source": "runtime", "index": index})
        if self._backend is not None:
            self._backend.put_success(item_id, value, metadata={"source": "runtime", "index": index})

    def evict(self, node_id: NodeId) -> None:
        """Demote a value out of the live tier (recoverable from the backend)."""
        if node_id in self.values:
            self.live_bytes -= estimate_bytes(self.values.pop(node_id))
        self._store.forget(node_id)

    def flush(self, timeout_s: float = 10.0) -> None:
        """Block until the persistence tier has drained."""
        self._store.flush(timeout_s=timeout_s)
