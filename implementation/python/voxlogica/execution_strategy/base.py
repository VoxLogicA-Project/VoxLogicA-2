"""Abstract execution strategy contract.

All runtime implementations consume the same symbolic plan but may differ in how
they evaluate, cache, or materialize results.
"""

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
    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None,
            apply_side_effects: bool = False) -> ExecutionResult:
        """Run compiled plan for selected goal nodes.

        Side effects (print/save) fire automatically when ``goals`` is None
        (a full, ordinary run). When ``goals`` restricts to a subset — e.g.
        a caller inspecting one value programmatically — side effects are
        suppressed by default (the caller presumably doesn't want a subset
        run to print/write as if it were the whole plan); pass
        ``apply_side_effects=True`` to fire them anyway for exactly the goals
        that were run (used by auto-sharding: each shard runs a real subset
        of the plan's goals and DOES want their ordinary print/save effects).
        """

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
