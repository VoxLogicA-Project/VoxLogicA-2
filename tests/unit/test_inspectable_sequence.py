from __future__ import annotations

import threading
import time

import voxlogica.inspectable_sequence as inspectable_sequence_mod
from voxlogica.inspectable_sequence import (
    InspectableIteratorSequence,
    InspectableMappedSequence,
    InspectableRangeSequence,
    InspectableSequenceValue,
    InspectableSubsequence,
)
from voxlogica.lazy.hash import hash_child_ref, hash_sequence_item
from voxlogica.value_model import adapt_runtime_value


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for predicate")


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
    assert second.state in {"queued", "running"}

    assert sequence.resolve_item(1) == 5
    assert produced == [3, 5]

    page = sequence.page_snapshot(0, 4)
    _wait_until(lambda: sequence.peek_item(2).state == "ready" or sequence.peek_item(3).state == "failed")
    page = sequence.page_snapshot(0, 4)
    assert [item.value for item in page["items"] if item.state == "ready"] == [3, 5, 8]
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
    assert item.state in {"queued", "running"}
    assert mapped.resolve_item(2) == 6
    assert calls == [3]

    page = mapped.page_snapshot(0, 2)
    _wait_until(lambda: all(mapped.peek_item(index).state == "ready" for index in (0, 1, 2)))
    assert [mapped.peek_item(index).value for index in (0, 1)] == [2, 4]
    assert calls == [3, 1, 2]
    assert page["items"][0].child_ref.child_id != page["items"][1].child_ref.child_id


def test_inspectable_mapped_sequence_reports_blocked_on_upstream_child() -> None:
    release = threading.Event()

    def _iterator():
        release.wait(0.2)
        yield 11

    source = InspectableIteratorSequence(parent_ref="source", iterator_factory=_iterator, total_size=1)
    mapped = InspectableMappedSequence(parent_ref="mapped", source=source, mapper=lambda value: value * 2)

    initial = mapped.ensure_item(0, priority="click")
    assert initial.state in {"queued", "running"}

    _wait_until(lambda: mapped.peek_item(0).state == "blocked")
    blocked = mapped.peek_item(0)
    assert blocked.blocked_on == source.child_ref(0).child_id
    assert blocked.state_reason in {"upstream:not_loaded", "upstream:queued", "upstream:running"}

    release.set()
    _wait_until(lambda: mapped.peek_item(0).state == "ready")
    assert mapped.peek_item(0).value == 22


def test_inspectable_subsequence_shares_upstream_items_by_index() -> None:
    source = InspectableRangeSequence(parent_ref="source", start=20, stop=30)
    sliced = InspectableSubsequence(parent_ref="slice", source=source, start=3, stop=6)

    page = sliced.page_snapshot(0, 5)
    _wait_until(lambda: all(sliced.peek_item(index).state == "ready" for index in (0, 1, 2)))
    page = sliced.page_snapshot(0, 5)

    assert [item.value for item in page["items"] if item.state == "ready"] == [23, 24, 25]
    assert page["total"] == 3
    assert page["has_more"] is False


def test_vox_iterator_sequence_uses_page_snapshot_when_available() -> None:
    sequence = InspectableRangeSequence(parent_ref="adapted", start=4, stop=9)
    adapted = adapt_runtime_value(sequence)
    page = adapted.page(offset=1, limit=3)

    assert [item["value"] for item in page.items] == [5, 6, 7]
    assert page.has_more is True
    assert page.total == 5


def test_wait_for_change_advances_runtime_version() -> None:
    sequence = InspectableIteratorSequence(parent_ref="change", iterator_factory=lambda: iter([1, 2]), total_size=2)
    version = sequence.version()
    snapshot = sequence.ensure_item(0)
    assert snapshot.state in {"queued", "running"}
    changed = sequence.wait_for_change(version, timeout=1.0)
    assert changed > version


def test_click_priority_promotes_already_queued_item(monkeypatch: pytest.MonkeyPatch) -> None:
    submissions: list[tuple[str, callable]] = []

    class _FakeScheduler:
        def submit(self, *, priority: str, callback) -> None:  # noqa: ANN001
            submissions.append((priority, callback))

    class _RecordingSequence(InspectableSequenceValue):
        def __init__(self) -> None:
            self.calls: list[str] = []
            super().__init__(parent_ref="recording-sequence", total_size=1)

        def _compute_item(self, index: int, priority: str) -> int:
            self.calls.append(priority)
            assert index == 0
            return 99

    monkeypatch.setattr(inspectable_sequence_mod, "_SCHEDULER", _FakeScheduler())
    sequence = _RecordingSequence()

    first = sequence.ensure_item(0, priority="visible-page")
    assert first.state == "queued"
    assert [priority for priority, _callback in submissions] == ["visible-page"]

    second = sequence.ensure_item(0, priority="click")
    assert second.state == "queued"
    assert [priority for priority, _callback in submissions] == ["visible-page", "click"]

    submissions[0][1]()
    assert sequence.peek_item(0).state == "queued"
    assert sequence.calls == []

    submissions[1][1]()
    assert sequence.peek_item(0).state == "ready"
    assert sequence.peek_item(0).value == 99
    assert sequence.calls == ["click"]


def test_blocked_mapped_item_resumes_with_requested_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    submissions: list[tuple[str, callable]] = []

    class _FakeScheduler:
        def submit(self, *, priority: str, callback) -> None:  # noqa: ANN001
            submissions.append((priority, callback))

    monkeypatch.setattr(inspectable_sequence_mod, "_SCHEDULER", _FakeScheduler())

    release = threading.Event()
    source = InspectableIteratorSequence(
        parent_ref="source-sequence",
        iterator_factory=lambda: iter([7]) if release.is_set() else iter(()),
        total_size=1,
    )
    mapped = InspectableMappedSequence(parent_ref="mapped-sequence", source=source, mapper=lambda value: value * 2)

    initial = mapped.ensure_item(0, priority="click")
    assert initial.state == "queued"
    assert submissions[0][0] == "click"

    submissions[0][1]()
    blocked = mapped.peek_item(0)
    assert blocked.state == "blocked"
    assert blocked.blocked_on == source.child_ref(0).child_id
    assert submissions[1][0] == "click"

    release.set()
    submissions[1][1]()

    assert [priority for priority, _callback in submissions] == ["click", "click", "click"]
    submissions[2][1]()
    assert mapped.peek_item(0).state == "ready"
    assert mapped.peek_item(0).value == 14


def test_scheduler_reserves_interactive_lane_while_siblings_wait_for_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = inspectable_sequence_mod._ChildTaskScheduler(workers=2)
    monkeypatch.setattr(inspectable_sequence_mod, "_SCHEDULER", scheduler)

    first_running = threading.Event()
    release_first = threading.Event()
    click_running = threading.Event()
    sibling_started = threading.Event()
    started: list[tuple[int, str]] = []
    started_lock = threading.Lock()

    class _BlockingSequence(InspectableSequenceValue):
        def __init__(self) -> None:
            super().__init__(parent_ref="blocking-sequence", total_size=3)

        def _compute_item(self, index: int, priority: str) -> int:
            with started_lock:
                started.append((index, priority))
            if index == 0:
                first_running.set()
                release_first.wait(timeout=2.0)
            elif index == 1:
                sibling_started.set()
            elif index == 2:
                click_running.set()
            return index

    sequence = _BlockingSequence()

    first = sequence.ensure_item(0, priority="visible-page")
    assert first.state == "queued"
    assert first_running.wait(timeout=1.0) is True

    second = sequence.ensure_item(1, priority="visible-page")
    assert second.state == "queued"
    time.sleep(0.1)
    assert sibling_started.is_set() is False
    assert sequence.peek_item(1).state == "queued"

    clicked = sequence.ensure_item(2, priority="click")
    assert clicked.state == "queued"
    assert click_running.wait(timeout=1.0) is True
    assert sibling_started.is_set() is False

    release_first.set()
    _wait_until(lambda: sequence.peek_item(1).state == "ready")

    assert started[:2] == [(0, "visible-page"), (2, "click")]
    assert started[2] == (1, "visible-page")
