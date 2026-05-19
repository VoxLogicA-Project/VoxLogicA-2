"""Shared execution data structures.

These types model the hand-off points between symbolic planning, runtime
evaluation, and user-facing reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
import time

from voxlogica.lazy.ir import NodeId, SymbolicPlan


class SequenceValue:
    """Lazily iterable sequence artifact with optional pagination hint."""

    def __init__(
        self,
        iterator_factory: Callable[[], Iterable[Any]],
        total_size: int | None = None,
    ):
        """Store a factory that can produce a fresh iterator on each access."""
        self._iterator_factory = iterator_factory
        self._total_size = total_size

    @classmethod
    def from_iterable(cls, iterable: Iterable[Any], total_size: int | None = None) -> "SequenceValue":
        """Materialize an iterable once and expose it through ``SequenceValue``."""
        cached = list(iterable)
        size = total_size if total_size is not None else len(cached)
        return cls(lambda: iter(cached), total_size=size)

    def iter_values(self) -> Iterable[Any]:
        """Return a new iterator over the sequence contents."""
        return self._iterator_factory()

    def page(self, offset: int, limit: int) -> list[Any]:
        """Read a half-open window from the sequence without exposing slicing."""
        if offset < 0 or limit < 0:
            raise ValueError("offset and limit must be non-negative")

        items: list[Any] = []
        index = 0
        for value in self.iter_values():
            if index < offset:
                index += 1
                continue
            if len(items) >= limit:
                break
            items.append(value)
            index += 1
        return items

    @property
    def total_size(self) -> int | None:
        """Return the known size hint, if the producer can provide one."""
        return self._total_size


@dataclass
class PreparedPlan:
    """Compiled plan plus the mutable state accumulated during one run."""

    plan: SymbolicPlan
    values: dict[NodeId, Any] = field(default_factory=dict)
    failures: dict[NodeId, str] = field(default_factory=dict)
    completed_nodes: set[NodeId] = field(default_factory=set)
    strategy_name: str = "strict"
    compiled_at: float = field(default_factory=time.time)


@dataclass
class PageResult:
    """Pagination payload for a sequence node."""

    items: list[Any]
    offset: int
    limit: int
    next_offset: int | None


@dataclass
class ExecutionResult:
    """Execution outcome payload shared by facade and CLI."""

    success: bool
    completed_operations: set[NodeId]
    failed_operations: dict[NodeId, str]
    execution_time: float
    total_operations: int
    cache_summary: dict[str, Any] = field(default_factory=dict)
    node_events: list[dict[str, Any]] = field(default_factory=list)
