"""Execution strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan


class ExecutionStrategy(ABC):
    """Strategy contract for plan compilation and execution."""

    name: str = "abstract"

    @abstractmethod
    def compile(self, plan: SymbolicPlan) -> PreparedPlan:
        """Compile symbolic plan to strategy-specific prepared representation."""

    @abstractmethod
    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None) -> ExecutionResult:
        """Run compiled plan for selected goal nodes."""

    @abstractmethod
    def stream(self, prepared: PreparedPlan, node: NodeId, chunk_size: int) -> Iterable[list[object]]:
        """Stream sequence output in chunks."""

    @abstractmethod
    def page(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        offset: int,
        limit: int,
    ) -> PageResult:
        """Return paginated sequence output."""
