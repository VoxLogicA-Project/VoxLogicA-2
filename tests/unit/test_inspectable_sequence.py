from __future__ import annotations

from voxlogica.inspectable_sequence import (
    InspectableIteratorSequence,
    InspectableMappedSequence,
    InspectableRangeSequence,
    InspectableSubsequence,
)
from voxlogica.lazy.hash import hash_child_ref, hash_sequence_item
from voxlogica.value_model import adapt_runtime_value


def test_hash_child_ref_matches_sequence_item_compatibility_wrapper() -> None:
    parent = "parent-node"
    assert hash_sequence_item(parent, 7) == hash_child_ref(parent, family="sequence-item-ref", token=7)


def test_inspectable_range_sequence_exposes_deterministic_child_refs() -> None:
    sequence = InspectableRangeSequence(parent_ref="node-range", start=10, stop=14)
    page = sequence.page_snapshot(0, 3)

    assert page["total"] == 4
    assert [item.value for item in page["items"]] == [10, 11, 12]
    assert [item.state for item in page["items"]] == ["ready", "ready", "ready"]
    assert page["items"][1].child_ref.child_id == hash_sequence_item("node-range", 1)


def test_inspectable_iterator_sequence_materializes_items_incrementally() -> None:
    produced: list[int] = []

    def _iterator():
        for value in [3, 5, 8]:
            produced.append(value)
            yield value

    sequence = InspectableIteratorSequence(parent_ref="node-iter", iterator_factory=_iterator, total_size=None)

    first = sequence.peek_item(0)
    assert first.state == "not_loaded"

    second = sequence.ensure_item(1)
    assert second.state == "ready"
    assert second.value == 5
    assert produced == [3, 5]

    page = sequence.page_snapshot(0, 4)
    assert [item.value for item in page["items"]] == [3, 5, 8]
    assert page["total"] == 3
    assert page["has_more"] is False


def test_inspectable_mapped_sequence_keeps_per_item_laziness() -> None:
    source = InspectableRangeSequence(parent_ref="range", start=1, stop=5)
    calls: list[int] = []

    def _double(value: int) -> int:
        calls.append(value)
        return value * 2

    mapped = InspectableMappedSequence(parent_ref="mapped", source=source, mapper=_double)

    item = mapped.ensure_item(2)
    assert item.state == "ready"
    assert item.value == 6
    assert calls == [3]

    page = mapped.page_snapshot(0, 2)
    assert [entry.value for entry in page["items"]] == [2, 4]
    assert calls == [3, 1, 2]


def test_inspectable_subsequence_shares_upstream_items_by_index() -> None:
    source = InspectableRangeSequence(parent_ref="source", start=20, stop=30)
    sliced = InspectableSubsequence(parent_ref="slice", source=source, start=3, stop=6)

    page = sliced.page_snapshot(0, 5)

    assert [item.value for item in page["items"]] == [23, 24, 25]
    assert page["total"] == 3
    assert page["has_more"] is False


def test_vox_iterator_sequence_uses_page_snapshot_when_available() -> None:
    sequence = InspectableRangeSequence(parent_ref="adapted", start=4, stop=9)
    adapted = adapt_runtime_value(sequence)
    page = adapted.page(offset=1, limit=3)

    assert [item["value"] for item in page.items] == [5, 6, 7]
    assert page.has_more is True
    assert page.total == 5
