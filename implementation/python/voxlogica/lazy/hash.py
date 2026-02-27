"""Deterministic hashing for symbolic nodes."""

from __future__ import annotations

from dataclasses import is_dataclass, fields
from typing import Any
import hashlib

import canonicaljson

from voxlogica.lazy.ir import NodeId, NodeSpec


def _normalize_value(value: Any) -> Any:
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
    return {
        "kind": node.kind,
        "operator": node.operator,
        "args": list(node.args),
        "kwargs": [[key, value] for key, value in sorted(node.kwargs)],
        "attrs": _normalize_value(node.attrs),
        "output_kind": node.output_kind,
    }


def hash_node(node: NodeSpec) -> NodeId:
    payload = node_payload(node)
    canonical = canonicaljson.encode_canonical_json(payload)
    return hashlib.sha256(canonical).hexdigest()


def hash_sequence_item(parent_node_id: str, index: int) -> NodeId:
    """Deterministically derive a child node id for one sequence element."""
    payload = {
        "kind": "sequence-item-ref",
        "parent_node_id": str(parent_node_id),
        "index": int(index),
    }
    canonical = canonicaljson.encode_canonical_json(payload)
    return hashlib.sha256(canonical).hexdigest()
