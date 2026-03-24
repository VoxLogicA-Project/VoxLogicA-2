"""Helpers for stable sequence-child node identities.

These helpers bridge runtime inspectable sequences and persisted sequence pages so
callers can resolve child node ids without hard-coding ``hash_sequence_item``.
"""

from __future__ import annotations

from typing import Any

from voxlogica.lazy.hash import hash_sequence_item
from voxlogica.value_model import adapt_runtime_value, normalize_path


def sequence_child_node_id(value: Any, *, parent_node_id: str, index: int) -> str:
    """Return the canonical node id for one sequence child.

    Inspectable sequences can define semantic child identities. Non-inspectable
    sequences fall back to the legacy parent-plus-index hash.
    """
    safe_index = int(index)
    raw = getattr(adapt_runtime_value(value), "raw", value)
    from voxlogica.inspectable_sequence import as_inspectable_sequence

    try:
        sequence = as_inspectable_sequence(raw, parent_ref=parent_node_id)
    except ValueError:
        return hash_sequence_item(parent_node_id, safe_index)
    return sequence.child_ref(safe_index).child_id


def stored_sequence_child_node_id(storage: Any, *, parent_node_id: str, index: int) -> str | None:
    """Read one persisted child node id from stored sequence pages when available."""
    safe_index = int(index)
    if safe_index < 0:
        return None

    getter_exact = getattr(storage, "get_page_record", None)
    if callable(getter_exact):
        exact = getter_exact(parent_node_id, "", safe_index, 1)
        node_id = _node_id_from_page_record(exact, expected_index=safe_index)
        if node_id is not None:
            return node_id

    getter_containing = getattr(storage, "get_page_containing_index", None)
    if callable(getter_containing):
        containing = getter_containing(parent_node_id, "", safe_index)
        node_id = _node_id_from_page_record(containing, expected_index=safe_index)
        if node_id is not None:
            return node_id

    return None


def resolve_sequence_reference(
    *,
    root_node_id: str,
    path: str | None,
    storage: Any | None = None,
    root_value: Any = None,
) -> tuple[str, str] | None:
    """Resolve the numeric-prefix node id for a sequence path.

    Returns the resolved node id plus the remaining non-sequence path suffix.
    """
    tokens = _path_tokens(path)
    if not tokens:
        return None

    current_node = str(root_node_id)
    current_value = root_value
    consumed = 0
    for position, token in enumerate(tokens):
        try:
            index = int(token)
        except ValueError:
            break
        if index < 0:
            break

        next_node: str | None = None
        if current_value is not None:
            next_node = sequence_child_node_id(current_value, parent_node_id=current_node, index=index)
        if next_node is None and storage is not None:
            next_node = stored_sequence_child_node_id(storage, parent_node_id=current_node, index=index)
        if next_node is None:
            next_node = hash_sequence_item(current_node, index)

        current_node = next_node
        consumed += 1

        if position >= len(tokens) - 1:
            continue

        next_value: Any = None
        if current_value is not None:
            next_value = _resolve_runtime_child_value(current_value, index=index, parent_node_id=current_node)
        if next_value is None and storage is not None:
            try:
                next_value = storage.get(current_node)
            except Exception:
                next_value = None
        current_value = next_value

    if consumed == 0:
        return None
    remaining_tokens = tokens[consumed:]
    remainder = "/" + "/".join(remaining_tokens) if remaining_tokens else ""
    return current_node, remainder


def resolve_sequence_container_node(
    *,
    root_node_id: str,
    path: str | None,
    storage: Any | None = None,
    root_value: Any = None,
) -> str | None:
    """Resolve the node id addressed by an all-numeric sequence path."""
    referenced = resolve_sequence_reference(
        root_node_id=root_node_id,
        path=path,
        storage=storage,
        root_value=root_value,
    )
    if referenced is None:
        return None
    node_id, remainder = referenced
    if remainder not in {"", "/"}:
        return None
    return node_id


def _node_id_from_page_record(page_record: Any, *, expected_index: int) -> str | None:
    if not isinstance(page_record, dict):
        return None
    items = page_record.get("items")
    if not isinstance(items, list):
        return None
    offset = int(page_record.get("offset", 0) or 0)
    local_index = expected_index - offset
    if local_index < 0 or local_index >= len(items):
        return None
    item = items[local_index]
    if not isinstance(item, dict):
        return None
    reference = item.get("__vox_ref__")
    if not isinstance(reference, dict):
        return None
    node_id = reference.get("node_id")
    return str(node_id) if isinstance(node_id, str) and node_id.strip() else None


def _path_tokens(path: str | None) -> list[str]:
    normalized = normalize_path(path)
    if not normalized:
        return []
    return [token for token in normalized.split("/") if token]


def _resolve_runtime_child_value(value: Any, *, index: int, parent_node_id: str) -> Any:
    raw = getattr(adapt_runtime_value(value), "raw", value)
    from voxlogica.inspectable_sequence import as_inspectable_sequence

    try:
        sequence = as_inspectable_sequence(raw, parent_ref=parent_node_id)
    except ValueError:
        return None
    try:
        return sequence.resolve_item(index)
    except Exception:
        return None