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


def _feed(h: "hashlib._Hash", value: Any) -> None:
    """Feed a normalized value into a hash incrementally — a fast, deterministic,
    collision-resistant canonical encoding (type tags + length-prefixed strings)
    that never builds a giant intermediate JSON string. This is what makes hashing
    a large plan (millions of nodes, some with deep attrs) cheap: the old
    json.dumps(sort_keys=True) rebuilt and re-serialized the whole payload per node,
    which dominated startup on full-dataset runs."""
    if value is None:
        h.update(b"N")
    elif value is True:
        h.update(b"T")
    elif value is False:
        h.update(b"F")
    elif isinstance(value, int):
        h.update(b"i"); h.update(str(value).encode("ascii"))
    elif isinstance(value, float):
        h.update(b"f"); h.update(repr(value).encode("ascii"))
    elif isinstance(value, str):
        e = value.encode("utf-8"); h.update(b"s"); h.update(str(len(e)).encode("ascii")); h.update(b":"); h.update(e)
    elif isinstance(value, (list, tuple)):
        h.update(b"[")
        for item in value:
            _feed(h, item)
        h.update(b"]")
    elif isinstance(value, dict):
        h.update(b"{")
        for k, v in sorted(value.items(), key=lambda kv: str(kv[0])):
            _feed(h, str(k)); _feed(h, v)
        h.update(b"}")
    else:
        s = str(value).encode("utf-8"); h.update(b"o"); h.update(str(len(s)).encode("ascii")); h.update(b":"); h.update(s)


def hash_node(node: NodeSpec) -> NodeId:
    """Hash one symbolic node into its stable DAG identifier."""
    h = hashlib.sha256()
    h.update(b"kind"); _feed(h, node.kind)
    h.update(b"op"); _feed(h, node.operator)
    h.update(b"args"); _feed(h, list(node.args))
    h.update(b"kw"); _feed(h, [[k, _normalize_value(v)] for k, v in sorted(node.kwargs)])
    h.update(b"attrs"); _feed(h, _normalize_value(node.attrs))
    h.update(b"ok"); _feed(h, node.output_kind)
    return h.hexdigest()


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
