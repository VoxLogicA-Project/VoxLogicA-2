"""Facade for symbolic DAG execution.

This module provides the stable entry point used by the CLI and any embedding
code. It accepts reducer output, normalizes it to the symbolic IR used by the
runtime, and delegates actual evaluation to one concrete execution strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import threading

from voxlogica.execution_strategy import ExecutionResult, PageResult, PreparedPlan, SequentialExecutionStrategy, ParallelExecutionStrategy
from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import NoCacheStorageBackend, StorageBackend, get_storage


@dataclass
class ExecutionStatus:
    """Minimal compatibility status payload."""

    running: bool
    completed: set[NodeId]
    failed: dict[NodeId, str]
    total: int
    progress: float


_operation_futures: dict[str, Any] = {}
_operation_futures_lock = threading.RLock()


def get_operation_future(operation_id: str) -> Any | None:
    """Return a compatibility future for an operation id, if one is tracked."""
    with _operation_futures_lock:
        return _operation_futures.get(operation_id)


def set_operation_future(operation_id: str, future: Any) -> bool:
    """Register a compatibility future unless the id is already present."""
    with _operation_futures_lock:
        if operation_id in _operation_futures:
            return False
        _operation_futures[operation_id] = future
        return True


def remove_operation_future(operation_id: str) -> None:
    """Forget any compatibility future associated with an operation id."""
    with _operation_futures_lock:
        _operation_futures.pop(operation_id, None)


class PrimitivesLoader:
    """Thin adapter around :class:`PrimitiveRegistry`.

    Keeping this wrapper isolates the execution facade from the full registry
    API and preserves a small compatibility surface for callers.
    """

    def __init__(self, registry: PrimitiveRegistry | None = None):
        """Create a loader backed by either the given or a fresh registry."""
        self.registry = registry or PrimitiveRegistry()

    def load_primitive(self, operator_name: str):
        """Load the runtime kernel for one primitive/operator name."""
        return self.registry.load_kernel(operator_name)

    def import_namespace(self, namespace_name: str) -> None:
        """Expose namespace import for older execution call sites."""
        self.registry.import_namespace(namespace_name)

    def list_namespaces(self) -> list[str]:
        """Return the namespaces visible through the backing registry."""
        return self.registry.list_namespaces()

    def list_primitives(self, namespace_name: str | None = None) -> dict[str, str]:
        """Return primitive descriptions from the backing registry."""
        return self.registry.list_primitives(namespace_name)


class ExecutionEngine:
    """Compile and execute symbolic plans through the selected strategy."""

    def __init__(
        self,
        primitives_loader: PrimitivesLoader | None = None,
        storage_backend: StorageBackend | None = None,
        no_cache: bool = False,
    ):
        """Create an engine bound to one primitive registry and one strategy."""
        self.primitives = primitives_loader or PrimitivesLoader()
        self.registry = self.primitives.registry
        self.storage = (storage_backend or get_storage())
        self._strategy = SequentialExecutionStrategy(self.registry, self.storage)
        self.default_strategy = self._strategy.name
        self._last_prepared: PreparedPlan | None = None

    def execute_workplan(
        self,
        workplan,
        execution_id: str | None = None,
        dask_dashboard: bool = False,
        strategy: str | None = None,
        goals: list[NodeId] | None = None,
    ) -> ExecutionResult:
        """Compile and immediately execute a work plan in one step."""
        del execution_id, dask_dashboard, strategy
        prepared = self.compile_plan(workplan)
        return self.run_prepared(prepared, goals=goals)

    def compile_plan(self, workplan, strategy: str | None = None) -> PreparedPlan:
        """Compile reducer output into a prepared execution object."""
        del strategy
        plan = self._to_symbolic_plan(workplan)
        prepared = self._strategy.compile(plan)
        self._last_prepared = prepared
        return prepared

    def run_prepared(
        self,
        prepared: PreparedPlan,
        *,
        goals: list[NodeId] | None = None,
        strategy: str | None = None,
    ) -> ExecutionResult:
        """Execute an already-prepared plan, optionally restricting the goals."""
        # print(self.storage)
        del strategy
        self._last_prepared = prepared
        # print(prepared)
        return self._strategy.run(prepared, goals=goals)

    def stream(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        chunk_size: int = 128,
        strategy: str | None = None,
    ):
        """Stream a sequence node in chunks via the underlying strategy."""
        del strategy
        return self._strategy.stream(prepared, node, chunk_size)

    def page(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        offset: int,
        limit: int,
        strategy: str | None = None,
    ) -> PageResult:
        """Return one page of items from a sequence-producing node."""
        del strategy
        return self._strategy.page(prepared, node, offset, limit)

    def _to_symbolic_plan(self, workplan) -> SymbolicPlan:
        """Normalize reducer output into the immutable symbolic execution IR."""
        if isinstance(workplan, SymbolicPlan):
            return workplan
        if hasattr(workplan, "to_symbolic_plan"):
            return workplan.to_symbolic_plan()
        raise TypeError("ExecutionEngine expected SymbolicPlan or WorkPlan with to_symbolic_plan()")


_execution_engine: ExecutionEngine | None = None


def get_execution_engine() -> ExecutionEngine:
    """Return the process-wide default execution engine singleton."""
    global _execution_engine
    if _execution_engine is None:
        _execution_engine = ExecutionEngine()
    return _execution_engine


def set_execution_engine(engine: ExecutionEngine):
    """Replace the process-wide default execution engine singleton."""
    global _execution_engine
    _execution_engine = engine


def execute_workplan(
    workplan,
    execution_id: str | None = None,
    dask_dashboard: bool = False,
    strategy: str | None = None,
    goals: list[NodeId] | None = None,
) -> ExecutionResult:
    """Compatibility helper that delegates to the shared execution engine."""
    return get_execution_engine().execute_workplan(
        workplan=workplan,
        execution_id=execution_id,
        dask_dashboard=dask_dashboard,
        strategy=strategy,
        goals=goals,
    )
