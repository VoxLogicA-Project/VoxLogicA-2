"""Facade for symbolic DAG execution.

This module provides the stable entry point used by the CLI and any embedding
code. It accepts reducer output, normalizes it to the symbolic IR used by the
runtime, and delegates actual evaluation to one concrete execution strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import threading

from voxlogica.execution_strategy import (
    ExecutionResult, PageResult, PreparedPlan, SequentialExecutionStrategy,
    LazyExecutionStrategy,
)
from voxlogica.execution_strategy import registry
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
        strategy: str | None = None,
        use_engine: bool = True,
        threads: int = 0,
        engine_debug: bool = False,
        dynamic_expansion: bool = True,
        threads_auto: str = "balanced",
        observe=None,
        sparse_cache: bool = False,
    ):
        """Create an engine bound to one primitive registry and one strategy.

        ``strategy`` names the runtime -- see
        ``execution_strategy.registry.available()`` for the choices, currently
        ``"engine"`` (the default), ``"lazy"`` and ``"sequential"``. An unknown
        name raises rather than quietly selecting the default.

        ``use_engine`` is the older boolean spelling, kept for callers that
        still pass it: ``False`` means ``strategy="lazy"``. An explicit
        ``strategy`` wins. Note the engine
        eagerly evicts intermediates, so — unlike lazy — it does not retain every
        binding's value in ``prepared.values`` after a run; read a value through a
        ``print``/``save`` goal instead. ``threads`` caps concurrent kernels for
        either (0 = auto-detect, see ``threads_auto``). See
        doc/dev/unified-computation-engine.md.

        ``observe`` is an optional ``(node_id, state, **fields)`` callable told
        about every node's transitions, so a UI can show a node computing while
        it computes. Engine strategy only, and a spectator by construction --
        see ``ComputationEngine._report``.

        ``sparse_cache`` stops the writer from persisting values that are
        already dead -- every consumer has run, so nothing in this run can ask
        for the value again and only a LATER run could reuse it. Off by default,
        because cross-run reuse is what makes an iterative session fast. Worth
        turning on for a large parameter sweep, where the intermediates are read
        exactly once and writing them is the bottleneck: see AsyncPersister.

        ``threads_auto`` picks the auto-detection heuristic used when
        ``threads=0`` (engine strategy only): ``"p-cores"`` (default) corrects
        for hybrid P/E CPUs, where os.cpu_count() overcounts (see
        engine/topology.py and doc/dev/free-threaded-handover.md's bandwidth
        section); ``"logical"`` restores the plain os.cpu_count() default.
        """
        self.primitives = primitives_loader or PrimitivesLoader()
        self.registry = self.primitives.registry
        self.storage = (storage_backend or get_storage())
        # Every strategy is built from the same keyword set; the registry passes
        # on only what each one declares, so this call does not need to know
        # which parameters belong to which runtime.
        self._build = dict(
            registry=self.registry, results_database=self.storage, threads=threads,
            debug=engine_debug, engine_debug=engine_debug, threads_auto=threads_auto,
            observe=observe, sparse_cache=sparse_cache,
            dynamic_expansion=dynamic_expansion,
        )
        chosen = strategy if strategy is not None else (registry.DEFAULT if use_engine else "lazy")
        self._strategy = self._instance(chosen)
        #: name -> instance, so a plan compiled under one name can still be run,
        #: paged or streamed without rebuilding its runtime.
        self.default_strategy = self._strategy.name
        self._last_prepared: PreparedPlan | None = None

    def _instance(self, name: str):
        """The strategy called ``name``, built once and reused."""
        cache = self.__dict__.setdefault("_instances", {})
        found = cache.get(name)
        if found is None:
            found = cache[name] = registry.create(name, **self._build)
        return found

    def _for(self, prepared: PreparedPlan, strategy: str | None):
        """The strategy that must serve this prepared plan.

        A ``PreparedPlan`` records the strategy that compiled it, and that
        record is authoritative: running one strategy's compilation on another
        is not a supported operation, and used to happen silently whenever a
        caller passed a name. An explicit name that disagrees is an error, not
        a preference.
        """
        name = prepared.strategy_name or self.default_strategy
        if strategy is not None and strategy != name:
            raise ValueError(
                f"this plan was compiled by the {name!r} strategy but {strategy!r} "
                f"was requested; recompile with compile_plan(strategy={strategy!r})"
            )
        return self._instance(name)

    def execute_workplan(
        self,
        workplan,
        execution_id: str | None = None,
        strategy: str | None = None,
        goals: list[NodeId] | None = None,
        profile: str | None = None,
    ) -> ExecutionResult:
        """Compile and immediately execute a work plan in one step."""
        del execution_id
        prepared = self.compile_plan(workplan, strategy=strategy)
        return self.run_prepared(prepared, goals=goals, strategy=strategy,
                                 profile=profile)

    def compile_plan(self, workplan, strategy: str | None = None) -> PreparedPlan:
        """Compile reducer output into a prepared execution object."""
        plan = self._to_symbolic_plan(workplan)
        prepared = self._instance(strategy or self.default_strategy).compile(plan)
        self._last_prepared = prepared
        return prepared

    def run_prepared(
        self,
        prepared: PreparedPlan,
        *,
        goals: list[NodeId] | None = None,
        strategy: str | None = None,
        profile: str | None = None,
    ) -> ExecutionResult:
        """Execute an already-prepared plan, optionally restricting the goals.

        ``profile`` is honored by ``EngineExecutionStrategy`` only (see its
        ``run()`` docstring); ``LazyExecutionStrategy`` ignores it.
        """
        self._last_prepared = prepared
        return self._for(prepared, strategy).run(prepared, goals=goals, profile=profile)

    def stream(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        chunk_size: int = 128,
        strategy: str | None = None,
    ):
        """Stream a sequence node in chunks via the underlying strategy."""
        return self._for(prepared, strategy).stream(prepared, node, chunk_size)

    def page(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        offset: int,
        limit: int,
        strategy: str | None = None,
    ) -> PageResult:
        """Return one page of items from a sequence-producing node."""
        return self._for(prepared, strategy).page(prepared, node, offset, limit)

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
    strategy: str | None = None,
    goals: list[NodeId] | None = None,
) -> ExecutionResult:
    """Compatibility helper that delegates to the shared execution engine."""
    return get_execution_engine().execute_workplan(
        workplan=workplan,
        execution_id=execution_id,
        strategy=strategy,
        goals=goals,
    )
