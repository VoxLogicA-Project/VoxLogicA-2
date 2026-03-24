"""Inspectable runtime sequences with deterministic child refs and per-item cache.

This module introduces a runtime contract above the existing ``SequenceValue``.
It does not change the language surface. Instead, sequence-producing operators can
return inspectable containers that expose child identities and item-by-item access
without forcing full materialization up front.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
import itertools
import os
import threading

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.lazy.hash import hash_child_ref


_DEFAULT_PRIORITY = "visible-page"
_PRIORITY_RANKS = {
    "click": 0,
    "focused-child": 0,
    "visible-page": 1,
    "background-fill": 2,
}


def _normalize_priority(priority: str | None) -> str:
    text = str(priority or _DEFAULT_PRIORITY).strip().lower()
    return text if text in _PRIORITY_RANKS else _DEFAULT_PRIORITY


def _priority_rank(priority: str | None) -> int:
    return _PRIORITY_RANKS[_normalize_priority(priority)]


class BlockedComputation(RuntimeError):
    """Signal that a child cannot compute until an upstream dependency changes."""

    def __init__(self, *, blocked_on: str, state_reason: str | None = None):
        self.blocked_on = str(blocked_on)
        self.state_reason = state_reason or "upstream-not-ready"
        super().__init__(self.state_reason)


class _ChildTaskScheduler:
    """Minimal priority scheduler for inspectable child tasks."""

    def __init__(self, *, workers: int | None = None):
        self._pending: list[tuple[int, int, Callable[[], None]]] = []
        self._counter = itertools.count()
        self._workers = []
        self._worker_count = max(2, min(8, workers or (os.cpu_count() or 4)))
        self._interactive_reserve = 1 if self._worker_count > 1 else 0
        self._active_noninteractive = 0
        self._condition = threading.Condition()
        for index in range(self._worker_count):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"vox-inspectable-sequence-{index}",
                daemon=True,
            )
            thread.start()
            self._workers.append(thread)

    def submit(self, *, priority: str, callback: Callable[[], None]) -> None:
        rank = _PRIORITY_RANKS.get(str(priority or _DEFAULT_PRIORITY), _PRIORITY_RANKS[_DEFAULT_PRIORITY])
        with self._condition:
            self._pending.append((rank, next(self._counter), callback))
            self._pending.sort(key=lambda item: (item[0], item[1]))
            self._condition.notify()

    def _pop_next_locked(self) -> tuple[int, int, Callable[[], None]] | None:
        if not self._pending:
            return None
        noninteractive_capacity = max(1, self._worker_count - self._interactive_reserve)
        for index, item in enumerate(self._pending):
            rank = int(item[0])
            if rank <= _PRIORITY_RANKS["focused-child"]:
                return self._pending.pop(index)
            if self._active_noninteractive < noninteractive_capacity:
                self._active_noninteractive += 1
                return self._pending.pop(index)
            return None
        return None

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                next_item = self._pop_next_locked()
                while next_item is None:
                    self._condition.wait()
                    next_item = self._pop_next_locked()
                rank, _ordinal, callback = next_item
            try:
                callback()
            except Exception:
                # Child task failures are translated into item snapshots inside the callback.
                pass
            finally:
                if rank > _PRIORITY_RANKS["focused-child"]:
                    with self._condition:
                        self._active_noninteractive = max(0, self._active_noninteractive - 1)
                        self._condition.notify_all()


_SCHEDULER = _ChildTaskScheduler()


@dataclass(frozen=True)
class ChildRef:
    """Deterministic reference for one inspectable child item."""

    family: str
    token: Any
    child_id: str


@dataclass(frozen=True)
class ItemSnapshot:
    """Snapshot of one child item state."""

    index: int
    child_ref: ChildRef
    state: str
    value: Any = None
    error: str | None = None
    blocked_on: str | None = None
    state_reason: str | None = None


class InspectableSequenceValue(SequenceValue, ABC):
    """SequenceValue with path-stable child refs and per-item inspection."""

    family = "sequence-item-ref"
    inline_items = False

    def __init__(self, *, parent_ref: str, total_size: int | None = None):
        self.parent_ref = str(parent_ref)
        self._state_lock = threading.RLock()
        self._change_condition = threading.Condition(self._state_lock)
        self._total_size = total_size
        self._cache: dict[int, Any] = {}
        self._errors: dict[int, str] = {}
        self._completed = False
        self._item_states: dict[int, dict[str, Any]] = {}
        self._version = 0
        self._listeners: list[Callable[[], None]] = []
        self._schedule_tokens = itertools.count()
        super().__init__(self.iter_values, total_size=total_size)

    def length_hint(self) -> int | None:
        """Return known child count when available."""
        return self._total_size

    def version(self) -> int:
        with self._state_lock:
            return self._version

    def wait_for_change(self, since_version: int, timeout: float | None = None) -> int:
        """Block until the sequence version changes, then return the new version."""
        with self._change_condition:
            if self._version != int(since_version):
                return self._version
            self._change_condition.wait(timeout=timeout)
            return self._version

    def add_change_listener(self, listener: Callable[[], None]) -> None:
        with self._state_lock:
            self._listeners.append(listener)

    def child_ref(self, index: int) -> ChildRef:
        """Return a deterministic child reference for one index."""
        safe_index = int(index)
        return ChildRef(
            family=self.family,
            token=safe_index,
            child_id=hash_child_ref(self.parent_ref, family=self.family, token=safe_index),
        )

    def peek_item(self, index: int) -> ItemSnapshot:
        """Return the current known state for one child without forcing compute."""
        safe_index = int(index)
        with self._state_lock:
            return self._snapshot_locked(safe_index)

    def ensure_item(self, index: int, priority: str = _DEFAULT_PRIORITY) -> ItemSnapshot:
        """Request one child item and return the current known snapshot."""
        safe_index = int(index)
        with self._state_lock:
            current = self._snapshot_locked(safe_index)
            if current.state in {"ready", "failed"}:
                return current
            if current.state == "blocked":
                self._schedule_locked(safe_index, priority)
                return self._snapshot_locked(safe_index)
            if self.inline_items:
                return self._compute_inline_locked(safe_index, priority)
            self._schedule_locked(safe_index, priority)
            return self._snapshot_locked(safe_index)

    def resolve_item(self, index: int, priority: str = _DEFAULT_PRIORITY) -> Any:
        """Resolve one child value or raise on failure/missing item."""
        safe_index = int(index)
        while True:
            snapshot = self.ensure_item(safe_index, priority=priority)
            if snapshot.state == "ready":
                return snapshot.value
            if snapshot.state == "failed":
                raise IndexError(snapshot.error or f"Unable to resolve child index {safe_index}")
            version = self.version()
            self.wait_for_change(version, timeout=5.0)

    def page_snapshot(
        self,
        offset: int,
        limit: int,
        priority: str = _DEFAULT_PRIORITY,
    ) -> dict[str, Any]:
        """Return one page worth of item snapshots."""
        safe_offset = max(0, int(offset))
        safe_limit = max(0, int(limit))
        items: list[ItemSnapshot] = []
        if safe_limit <= 0:
            total = self.length_hint()
            return {
                "offset": safe_offset,
                "limit": safe_limit,
                "items": items,
                "next_offset": None,
                "has_more": bool(total is None or safe_offset < total),
                "total": total,
            }

        for absolute in range(safe_offset, safe_offset + safe_limit):
            snapshot = self.peek_item(absolute)
            if snapshot.state in {"not_loaded", "blocked"}:
                snapshot = self.ensure_item(absolute, priority=priority)
            if snapshot.state == "failed" and snapshot.state_reason == "out-of-range":
                break
            items.append(snapshot)

        total = self.length_hint()
        if total is None:
            has_more = len(items) == safe_limit
        else:
            has_more = safe_offset + len(items) < total
        next_offset = safe_offset + len(items) if has_more else None
        return {
            "offset": safe_offset,
            "limit": safe_limit,
            "items": items,
            "next_offset": next_offset,
            "has_more": bool(has_more),
            "total": total,
        }

    def page(self, offset: int, limit: int) -> list[Any]:
        """Compatibility page API used by existing SequenceValue callers."""
        snapshot = self.page_snapshot(offset, limit)
        return [item.value for item in snapshot["items"] if item.state == "ready"]

    def iter_values(self) -> Iterable[Any]:
        index = 0
        while True:
            try:
                yield self.resolve_item(index)
            except IndexError:
                break
            index += 1

    @abstractmethod
    def _compute_item(self, index: int, priority: str) -> Any:
        """Compute one child value or raise ``IndexError`` when absent."""

    def _snapshot_locked(self, index: int) -> ItemSnapshot:
        child = self.child_ref(index)
        if index in self._errors:
            return ItemSnapshot(
                index=index,
                child_ref=child,
                state="failed",
                error=self._errors[index],
                state_reason=self._item_states.get(index, {}).get("state_reason"),
            )
        if index in self._cache:
            return ItemSnapshot(
                index=index,
                child_ref=child,
                state="ready",
                value=self._cache[index],
            )
        if self._total_size is not None and index >= self._total_size:
            return ItemSnapshot(
                index=index,
                child_ref=child,
                state="failed",
                error="index out of range",
                state_reason="out-of-range",
            )
        meta = self._item_states.get(index)
        if meta is None:
            return ItemSnapshot(index=index, child_ref=child, state="not_loaded")
        return ItemSnapshot(
            index=index,
            child_ref=child,
            state=str(meta.get("state") or "not_loaded"),
            blocked_on=meta.get("blocked_on"),
            state_reason=meta.get("state_reason"),
            error=meta.get("error"),
        )

    def _compute_inline_locked(self, index: int, priority: str) -> ItemSnapshot:
        del priority
        try:
            value = self._compute_item(index, _DEFAULT_PRIORITY)
        except IndexError:
            if self._total_size is None:
                self._total_size = len(self._cache)
                self._completed = True
            self._errors[index] = "index out of range"
            self._item_states[index] = {"state": "failed", "state_reason": "out-of-range", "error": "index out of range"}
            self._notify_change_locked()
            return self._snapshot_locked(index)
        except BlockedComputation as exc:
            self._item_states[index] = {
                "state": "blocked",
                "blocked_on": exc.blocked_on,
                "state_reason": exc.state_reason,
                "_requested_priority": _DEFAULT_PRIORITY,
                "_requested_rank": _priority_rank(_DEFAULT_PRIORITY),
            }
            self._notify_change_locked()
            return self._snapshot_locked(index)
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            self._errors[index] = message
            self._item_states[index] = {"state": "failed", "error": message}
            self._notify_change_locked()
            return self._snapshot_locked(index)
        self._cache[index] = value
        self._item_states.pop(index, None)
        self._notify_change_locked()
        return self._snapshot_locked(index)

    def _schedule_locked(self, index: int, priority: str) -> None:
        current = self._item_states.get(index, {})
        state = str(current.get("state") or "not_loaded")
        normalized_priority = _normalize_priority(priority)
        requested_rank = _priority_rank(normalized_priority)
        current_rank = int(current.get("_requested_rank", _PRIORITY_RANKS[_DEFAULT_PRIORITY]))
        if state == "running":
            if requested_rank < current_rank:
                self._item_states[index] = {
                    **current,
                    "_requested_priority": normalized_priority,
                    "_requested_rank": requested_rank,
                    "state_reason": f"priority:{normalized_priority}",
                }
                self._notify_change_locked()
            return
        if state == "queued" and requested_rank >= current_rank:
            return
        token = next(self._schedule_tokens)
        self._item_states[index] = {
            **current,
            "state": "queued",
            "state_reason": f"priority:{normalized_priority}",
            "_requested_priority": normalized_priority,
            "_requested_rank": requested_rank,
            "_schedule_token": token,
        }
        self._notify_change_locked()
        _SCHEDULER.submit(
            priority=normalized_priority,
            callback=lambda: self._run_scheduled_item(index, normalized_priority, token),
        )

    def _run_scheduled_item(self, index: int, priority: str, token: int) -> None:
        with self._state_lock:
            current = self._snapshot_locked(index)
            if current.state in {"ready", "failed"}:
                return
            meta = self._item_states.get(index, {})
            current_token = meta.get("_schedule_token")
            if current_token is not None and int(current_token) != int(token):
                return
            requested_priority = _normalize_priority(meta.get("_requested_priority", priority))
            requested_rank = int(meta.get("_requested_rank", _priority_rank(requested_priority)))
            self._item_states[index] = {
                **meta,
                "state": "running",
                "state_reason": f"priority:{requested_priority}",
                "_requested_priority": requested_priority,
                "_requested_rank": requested_rank,
                "_schedule_token": token,
            }
            self._notify_change_locked()
        try:
            value = self._compute_item(index, requested_priority)
        except IndexError:
            with self._state_lock:
                if self._total_size is None:
                    self._total_size = min(index, len(self._cache))
                    self._completed = True
                self._errors[index] = "index out of range"
                self._item_states[index] = {
                    "state": "failed",
                    "state_reason": "out-of-range",
                    "error": "index out of range",
                    "_requested_priority": requested_priority,
                    "_requested_rank": requested_rank,
                }
                self._reconcile_materialized_locked()
                self._notify_change_locked()
            return
        except BlockedComputation as exc:
            with self._state_lock:
                self._item_states[index] = {
                    "state": "blocked",
                    "blocked_on": exc.blocked_on,
                    "state_reason": exc.state_reason,
                    "_requested_priority": requested_priority,
                    "_requested_rank": requested_rank,
                }
                self._notify_change_locked()
            return
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            with self._state_lock:
                self._errors[index] = message
                self._item_states[index] = {
                    "state": "failed",
                    "error": message,
                    "_requested_priority": requested_priority,
                    "_requested_rank": requested_rank,
                }
                self._reconcile_materialized_locked()
                self._notify_change_locked()
            return

        with self._state_lock:
            self._item_states[index] = {
                "state": "persisting",
                "state_reason": "runtime-cache",
                "_requested_priority": requested_priority,
                "_requested_rank": requested_rank,
            }
            self._notify_change_locked()
            self._cache[index] = value
            self._reconcile_materialized_locked()
            self._notify_change_locked()

    def _reconcile_materialized_locked(self) -> None:
        for index in list(self._item_states.keys()):
            if index in self._cache or index in self._errors:
                self._item_states.pop(index, None)

    def _notify_change_locked(self) -> None:
        self._version += 1
        self._change_condition.notify_all()
        listeners = list(self._listeners)
        self._state_lock.release()
        try:
            for listener in listeners:
                try:
                    listener()
                except Exception:
                    continue
        finally:
            self._state_lock.acquire()


class InspectableRangeSequence(InspectableSequenceValue):
    """Inspectable integer range with random access."""

    inline_items = True

    def __init__(self, *, parent_ref: str, start: int, stop: int):
        self._start = int(start)
        self._stop = int(stop)
        total = max(0, self._stop - self._start)
        super().__init__(parent_ref=parent_ref, total_size=total)

    def _compute_item(self, index: int, priority: str) -> Any:
        del priority
        safe_index = int(index)
        if safe_index < 0 or self._start + safe_index >= self._stop:
            raise IndexError(safe_index)
        return self._start + safe_index


class InspectableListSequence(InspectableSequenceValue):
    """Inspectable sequence backed by an already-known list."""

    inline_items = True

    def __init__(self, *, parent_ref: str, values: Iterable[Any]):
        self._values = list(values)
        super().__init__(parent_ref=parent_ref, total_size=len(self._values))

    def _compute_item(self, index: int, priority: str) -> Any:
        del priority
        safe_index = int(index)
        if safe_index < 0 or safe_index >= len(self._values):
            raise IndexError(safe_index)
        return self._values[safe_index]


class InspectableIteratorSequence(InspectableSequenceValue):
    """Inspectable adapter over an iterator-style lazy sequence."""

    def __init__(
        self,
        *,
        parent_ref: str,
        iterator_factory: Callable[[], Iterable[Any]],
        total_size: int | None = None,
    ):
        self._source_factory = iterator_factory
        self._source_iter = None
        self._exhausted = False
        super().__init__(parent_ref=parent_ref, total_size=total_size)

    def _ensure_source_advanced_to(self, index: int) -> None:
        while len(self._cache) <= index and not self._exhausted:
            if self._source_iter is None:
                self._source_iter = iter(self._source_factory())
            try:
                next_value = next(self._source_iter)
            except StopIteration as exc:
                self._exhausted = True
                self._completed = True
                self._source_iter = None
                if self._total_size is None:
                    self._total_size = len(self._cache)
                raise IndexError(index) from exc
            self._cache[len(self._cache)] = next_value

    def _compute_item(self, index: int, priority: str) -> Any:
        del priority
        safe_index = int(index)
        if safe_index < 0:
            raise IndexError(safe_index)
        with self._state_lock:
            if safe_index in self._cache:
                return self._cache[safe_index]
            self._ensure_source_advanced_to(safe_index)
            return self._cache[safe_index]


def as_inspectable_sequence(value: Any, *, parent_ref: str) -> InspectableSequenceValue:
    """Adapt a sequence-like runtime value into an inspectable container."""
    if isinstance(value, InspectableSequenceValue):
        return value
    if isinstance(value, SequenceValue):
        return InspectableIteratorSequence(
            parent_ref=parent_ref,
            iterator_factory=value.iter_values,
            total_size=value.total_size,
        )
    if isinstance(value, range):
        return InspectableRangeSequence(parent_ref=parent_ref, start=value.start, stop=value.stop)
    if isinstance(value, (list, tuple)):
        return InspectableListSequence(parent_ref=parent_ref, values=value)
    if isinstance(value, Path):
        return InspectableListSequence(parent_ref=parent_ref, values=[str(value)])
    if hasattr(value, "__iter__"):
        return InspectableIteratorSequence(parent_ref=parent_ref, iterator_factory=lambda: iter(value))
    raise ValueError(f"Value is not a sequence: {type(value).__name__}")


class InspectableMappedSequence(InspectableSequenceValue):
    """Inspectable per-item transform over another sequence."""

    def __init__(
        self,
        *,
        parent_ref: str,
        source: Any,
        mapper: Callable[[Any], Any],
    ):
        self._source = as_inspectable_sequence(source, parent_ref=f"{parent_ref}:source")
        self._mapper = mapper
        self._source.add_change_listener(self._on_source_change)
        super().__init__(parent_ref=parent_ref, total_size=self._source.length_hint())

    def _compute_item(self, index: int, priority: str) -> Any:
        upstream_snapshot = self._source.peek_item(index)
        if upstream_snapshot.state != "ready":
            upstream_snapshot = self._source.ensure_item(index, priority=priority)
        if upstream_snapshot.state == "ready":
            upstream = upstream_snapshot.value
            item_runtime_ref = hash_child_ref(self.parent_ref, family="mapped-item", token=int(index))
            if hasattr(self._mapper, "apply_with_ref") and callable(self._mapper.apply_with_ref):
                return self._mapper.apply_with_ref(upstream, runtime_ref=item_runtime_ref)
            if hasattr(self._mapper, "invoke") and callable(self._mapper.invoke):
                return self._mapper.invoke([upstream], runtime_ref=item_runtime_ref)
            if hasattr(self._mapper, "apply") and callable(self._mapper.apply):
                return self._mapper.apply(upstream)
            if callable(self._mapper):
                return self._mapper(upstream)
            raise ValueError("map closure is not callable")
        if upstream_snapshot.state == "failed":
            raise RuntimeError(upstream_snapshot.error or f"Upstream item {index} failed")
        self._source.ensure_item(index, priority=priority)
        raise BlockedComputation(
            blocked_on=self._source.child_ref(index).child_id,
            state_reason=f"upstream:{upstream_snapshot.state}",
        )

    def _on_source_change(self) -> None:
        to_resume: list[tuple[int, str]] = []
        with self._state_lock:
            for index, meta in self._item_states.items():
                if str(meta.get("state")) != "blocked":
                    continue
                upstream_snapshot = self._source.peek_item(index)
                if upstream_snapshot.state in {"ready", "failed"}:
                    to_resume.append((int(index), _normalize_priority(meta.get("_requested_priority", _DEFAULT_PRIORITY))))
        for index, priority in to_resume:
            self.ensure_item(index, priority=priority)


class InspectableSubsequence(InspectableSequenceValue):
    """Inspectable view over a contiguous subsequence."""

    def __init__(
        self,
        *,
        parent_ref: str,
        source: Any,
        start: int,
        stop: int,
    ):
        self._source = as_inspectable_sequence(source, parent_ref=f"{parent_ref}:source")
        self._start = max(0, int(start))
        self._stop = max(self._start, int(stop))
        self._source.add_change_listener(self._on_source_change)
        source_length = self._source.length_hint()
        total_size: int | None = None
        if source_length is not None:
            total_size = max(0, min(self._stop, source_length) - min(self._start, source_length))
        super().__init__(parent_ref=parent_ref, total_size=total_size)

    def _compute_item(self, index: int, priority: str) -> Any:
        safe_index = int(index)
        if safe_index < 0:
            raise IndexError(safe_index)
        absolute_index = self._start + safe_index
        if absolute_index >= self._stop:
            raise IndexError(safe_index)
        upstream_snapshot = self._source.peek_item(absolute_index)
        if upstream_snapshot.state != "ready":
            upstream_snapshot = self._source.ensure_item(absolute_index, priority=priority)
        if upstream_snapshot.state == "ready":
            return upstream_snapshot.value
        if upstream_snapshot.state == "failed":
            raise RuntimeError(upstream_snapshot.error or f"Upstream item {absolute_index} failed")
        self._source.ensure_item(absolute_index, priority=priority)
        raise BlockedComputation(
            blocked_on=self._source.child_ref(absolute_index).child_id,
            state_reason=f"upstream:{upstream_snapshot.state}",
        )

    def _on_source_change(self) -> None:
        to_resume: list[tuple[int, str]] = []
        with self._state_lock:
            for index, meta in self._item_states.items():
                if str(meta.get("state")) != "blocked":
                    continue
                absolute_index = self._start + int(index)
                upstream_snapshot = self._source.peek_item(absolute_index)
                if upstream_snapshot.state in {"ready", "failed"}:
                    to_resume.append((int(index), _normalize_priority(meta.get("_requested_priority", _DEFAULT_PRIORITY))))
        for index, priority in to_resume:
            self.ensure_item(index, priority=priority)
