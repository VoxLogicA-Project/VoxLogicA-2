"""Runtime facade with pluggable execution strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import threading

from voxlogica.execution_strategy import (
    DaskExecutionStrategy,
    ExecutionResult,
    PageResult,
    PreparedPlan,
    StrictExecutionStrategy,
)
from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry


@dataclass
class ExecutionStatus:
    """Minimal compatibility status payload."""

    running: bool
    completed: set[NodeId]
    failed: dict[NodeId, str]
    total: int
    progress: float


# Compatibility futures table for existing imports.
_operation_futures: dict[str, Any] = {}
_operation_futures_lock = threading.RLock()


def get_operation_future(operation_id: str) -> Optional[Any]:
    with _operation_futures_lock:
        return _operation_futures.get(operation_id)


def set_operation_future(operation_id: str, future: Any) -> bool:
    with _operation_futures_lock:
        if operation_id in _operation_futures:
            return False
        _operation_futures[operation_id] = future
        return True


def remove_operation_future(operation_id: str) -> None:
    with _operation_futures_lock:
        _operation_futures.pop(operation_id, None)


class PrimitivesLoader:
    """Compatibility wrapper around the deterministic PrimitiveRegistry."""

    def __init__(self, registry: PrimitiveRegistry | None = None):
        self.registry = registry or PrimitiveRegistry()

    def load_primitive(self, operator_name: str):
        return self.registry.load_kernel(operator_name)

    def import_namespace(self, namespace_name: str) -> None:
        self.registry.import_namespace(namespace_name)

    def list_namespaces(self) -> list[str]:
        return self.registry.list_namespaces()

    def list_primitives(self, namespace_name: str | None = None) -> dict[str, str]:
        return self.registry.list_primitives(namespace_name)


class ExecutionEngine:
    """Execution facade that routes to selected strategy."""

    def __init__(
        self,
        storage_backend: Any | None = None,
        primitives_loader: PrimitivesLoader | None = None,
        auto_cleanup_stale_operations: bool = True,
    ):
        self.storage = storage_backend
        self.primitives = primitives_loader or PrimitivesLoader()
        self.registry = self.primitives.registry

        self._strategies = {
            "dask": DaskExecutionStrategy(self.registry),
            "strict": StrictExecutionStrategy(self.registry),
        }
        self.default_strategy = "dask"

        self._last_prepared: PreparedPlan | None = None

    def execute_workplan(
        self,
        workplan,
        execution_id: Optional[str] = None,
        dask_dashboard: bool = False,
        strategy: str | None = None,
    ) -> ExecutionResult:
        selected = strategy or self.default_strategy
        if selected not in self._strategies:
            raise ValueError(
                f"Unknown execution strategy '{selected}'. "
                f"Available: {sorted(self._strategies.keys())}"
            )

        plan = self._to_symbolic_plan(workplan)
        execution_strategy = self._strategies[selected]
        prepared = execution_strategy.compile(plan)
        self._last_prepared = prepared

        return execution_strategy.run(prepared, goals=None)

    def compile_plan(self, workplan, strategy: str | None = None) -> PreparedPlan:
        selected = strategy or self.default_strategy
        if selected not in self._strategies:
            raise ValueError(
                f"Unknown execution strategy '{selected}'. "
                f"Available: {sorted(self._strategies.keys())}"
            )

        plan = self._to_symbolic_plan(workplan)
        prepared = self._strategies[selected].compile(plan)
        self._last_prepared = prepared
        return prepared

    def stream(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        chunk_size: int = 128,
        strategy: str | None = None,
    ):
        selected = strategy or prepared.strategy_name
        return self._strategies[selected].stream(prepared, node, chunk_size)

    def page(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        offset: int,
        limit: int,
        strategy: str | None = None,
    ) -> PageResult:
        selected = strategy or prepared.strategy_name
        return self._strategies[selected].page(prepared, node, offset, limit)

    def _to_symbolic_plan(self, workplan) -> SymbolicPlan:
        if isinstance(workplan, SymbolicPlan):
            return workplan

        if hasattr(workplan, "to_symbolic_plan"):
            return workplan.to_symbolic_plan()

        raise TypeError(
            "ExecutionEngine expected SymbolicPlan or WorkPlan with to_symbolic_plan()"
        )


def get_shared_dask_client(enable_dashboard: bool = False):
    """Compatibility stub retained for old imports."""
    return None


def close_shared_dask_client():
    """Compatibility stub retained for old imports."""
    return None


# Global execution engine instance
_execution_engine: Optional[ExecutionEngine] = None


def get_execution_engine() -> ExecutionEngine:
    global _execution_engine
    if _execution_engine is None:
        _execution_engine = ExecutionEngine()
    return _execution_engine


def set_execution_engine(engine: ExecutionEngine):
    global _execution_engine
    _execution_engine = engine


def execute_workplan(
    workplan,
    execution_id: Optional[str] = None,
    dask_dashboard: bool = False,
    strategy: str | None = None,
) -> ExecutionResult:
    return get_execution_engine().execute_workplan(
        workplan=workplan,
        execution_id=execution_id,
        dask_dashboard=dask_dashboard,
        strategy=strategy,
    )
