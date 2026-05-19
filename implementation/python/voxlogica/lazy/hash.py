"""Deterministic hashing for symbolic nodes and child references.

Stable hashes let the reducer deduplicate equivalent nodes and let the runtime
cache results by structural identity rather than by construction order.
"""

from __future__ import annotations

from dataclasses import is_dataclass, fields
from typing import Any
import hashlib
import json

from voxlogica.lazy.ir import NodeId, NodeSpec


def _normalize_value(value: Any) -> Any:
    """Convert rich Python objects into hash-stable JSON-compatible values."""
    if hasattr(value, "to_syntax") and callable(value.to_syntax):
        return value.to_syntax()

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _normalize_value(getattr(value, field.name)) for field in fields(value)}

    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}

    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(item) for item in value]

    return value


def node_payload(node: NodeSpec) -> dict[str, Any]:
    """Build the canonical payload that represents one symbolic node."""
    return {
        "kind": node.kind,
        "operator": node.operator,
        "args": list(node.args),
        "kwargs": [[key, value] for key, value in sorted(node.kwargs)],
        "attrs": _normalize_value(node.attrs),
        "output_kind": node.output_kind,
    }


def hash_node(node: NodeSpec) -> NodeId:
    """Hash one symbolic node into its stable DAG identifier."""
    payload = node_payload(node)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_sequence_item(parent_node_id: str, index: int) -> NodeId:
    """Deterministically derive a child node id for one sequence element."""
    return hash_child_ref(parent_node_id, family="sequence-item-ref", token=int(index))


def hash_child_ref(parent_node_id: str, *, family: str, token: Any) -> NodeId:
    """Deterministically derive a child ref from a parent id and local token."""
    payload = {
        "kind": str(family),
        "parent_node_id": str(parent_node_id),
        "token": _normalize_value(token),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
