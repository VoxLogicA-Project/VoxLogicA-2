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
import threading

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.lazy.hash import hash_child_ref


_DEFAULT_PRIORITY = "visible-page"


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

    def __init__(self, *, parent_ref: str, total_size: int | None = None):
        self.parent_ref = str(parent_ref)
        self._state_lock = threading.RLock()
        self._total_size = total_size
        self._cache: dict[int, Any] = {}
        self._errors: dict[int, str] = {}
        self._completed = False
        super().__init__(self.iter_values, total_size=total_size)

    def length_hint(self) -> int | None:
        """Return known child count when available."""
        return self._total_size

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
            child = self.child_ref(safe_index)
            if safe_index in self._errors:
                return ItemSnapshot(
                    index=safe_index,
                    child_ref=child,
                    state="failed",
                    error=self._errors[safe_index],
                )
            if safe_index in self._cache:
                return ItemSnapshot(
                    index=safe_index,
                    child_ref=child,
                    state="ready",
                    value=self._cache[safe_index],
                )
            if self._total_size is not None and safe_index >= self._total_size:
                return ItemSnapshot(
                    index=safe_index,
                    child_ref=child,
                    state="failed",
                    error="index out of range",
                    state_reason="out-of-range",
                )
            return ItemSnapshot(index=safe_index, child_ref=child, state="not_loaded")

    def ensure_item(self, index: int, priority: str = _DEFAULT_PRIORITY) -> ItemSnapshot:
        """Compute one child on demand and cache it."""
        del priority
        safe_index = int(index)
        current = self.peek_item(safe_index)
        if current.state in {"ready", "failed"}:
            return current
        try:
            value = self._compute_item(safe_index)
        except IndexError:
            if self._total_size is None:
                with self._state_lock:
                    self._total_size = len(self._cache)
                    self._completed = True
            return ItemSnapshot(
                index=safe_index,
                child_ref=self.child_ref(safe_index),
                state="failed",
                error="index out of range",
                state_reason="out-of-range",
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            with self._state_lock:
                self._errors[safe_index] = message
            return ItemSnapshot(
                index=safe_index,
                child_ref=self.child_ref(safe_index),
                state="failed",
                error=message,
            )
        with self._state_lock:
            self._cache[safe_index] = value
        return ItemSnapshot(
            index=safe_index,
            child_ref=self.child_ref(safe_index),
            state="ready",
            value=value,
        )

    def resolve_item(self, index: int, priority: str = _DEFAULT_PRIORITY) -> Any:
        """Resolve one child value or raise on failure/missing item."""
        snapshot = self.ensure_item(index, priority=priority)
        if snapshot.state != "ready":
            raise IndexError(snapshot.error or f"Unable to resolve child index {index}")
        return snapshot.value

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
            if snapshot.state == "not_loaded":
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
            snapshot = self.ensure_item(index)
            if snapshot.state != "ready":
                break
            yield snapshot.value
            index += 1

    @abstractmethod
    def _compute_item(self, index: int) -> Any:
        """Compute one child value or raise ``IndexError`` when absent."""


class InspectableRangeSequence(InspectableSequenceValue):
    """Inspectable integer range with random access."""

    def __init__(self, *, parent_ref: str, start: int, stop: int):
        self._start = int(start)
        self._stop = int(stop)
        total = max(0, self._stop - self._start)
        super().__init__(parent_ref=parent_ref, total_size=total)

    def _compute_item(self, index: int) -> Any:
        safe_index = int(index)
        if safe_index < 0 or self._start + safe_index >= self._stop:
            raise IndexError(safe_index)
        return self._start + safe_index


class InspectableListSequence(InspectableSequenceValue):
    """Inspectable sequence backed by an already-known list."""

    def __init__(self, *, parent_ref: str, values: Iterable[Any]):
        self._values = list(values)
        super().__init__(parent_ref=parent_ref, total_size=len(self._values))

    def _compute_item(self, index: int) -> Any:
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

    def _compute_item(self, index: int) -> Any:
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
        super().__init__(parent_ref=parent_ref, total_size=self._source.length_hint())

    def _compute_item(self, index: int) -> Any:
        upstream_snapshot = self._source.ensure_item(index)
        if upstream_snapshot.state != "ready":
            raise IndexError(index)
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
        source_length = self._source.length_hint()
        total_size: int | None = None
        if source_length is not None:
            total_size = max(0, min(self._stop, source_length) - min(self._start, source_length))
        super().__init__(parent_ref=parent_ref, total_size=total_size)

    def _compute_item(self, index: int) -> Any:
        safe_index = int(index)
        if safe_index < 0:
            raise IndexError(safe_index)
        absolute_index = self._start + safe_index
        if absolute_index >= self._stop:
            raise IndexError(safe_index)
        return self._source.resolve_item(absolute_index)
