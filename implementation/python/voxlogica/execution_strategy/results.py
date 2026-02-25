"""Shared result and prepared-plan contracts for execution strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
import time

from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.storage import DefinitionStore, MaterializationStore


class SequenceValue:
    """Lazily iterable sequence artifact with optional pagination hint."""

    def __init__(
        self,
        iterator_factory: Callable[[], Iterable[Any]],
        total_size: int | None = None,
    ):
        self._iterator_factory = iterator_factory
        self._total_size = total_size

    @classmethod
    def from_iterable(cls, iterable: Iterable[Any], total_size: int | None = None) -> "SequenceValue":
        cached = list(iterable)
        return cls(lambda: iter(cached), total_size=total_size if total_size is not None else len(cached))

    def iter_values(self) -> Iterable[Any]:
        return self._iterator_factory()

    def page(self, offset: int, limit: int) -> list[Any]:
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
        return self._total_size


@dataclass
class PreparedPlan:
    """Compiled plan representation used by an execution strategy."""

    plan: SymbolicPlan
    definition_store: DefinitionStore
    materialization_store: MaterializationStore
    strategy_name: str
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
    """Execution outcome payload shared by facade and features."""

    success: bool
    completed_operations: set[NodeId]
    failed_operations: dict[NodeId, str]
    execution_time: float
    total_operations: int
    cache_summary: dict[str, Any] = field(default_factory=dict)
    node_events: list[dict[str, Any]] = field(default_factory=list)
