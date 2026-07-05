"""Adapter exposing the computation engine as an execution strategy.

Lets the existing CLI/facade drive the engine through the same
``compile``/``run`` surface as the other strategies: a one-shot ``run`` submits
every goal of the plan, evaluates them in parallel, then applies their
print/save side effects from the materialized results.
"""

from __future__ import annotations

import asyncio
import json
import pickle
import time
from pathlib import Path
from typing import Any

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.priority import Priority
from voxlogica.execution_strategy.results import ExecutionResult, PreparedPlan, SequenceValue
from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import StorageBackend


class EngineExecutionStrategy:
    """Evaluates a plan as a batch of engine queries (one per goal)."""

    name = "engine"

    def __init__(self, registry: PrimitiveRegistry | None = None, results_database: StorageBackend | None = None,
                 threads: int = 0, debug: bool = False):
        self.registry = registry or PrimitiveRegistry()
        self.results_database = results_database
        self.threads = threads
        self.debug = debug

    def compile(self, plan: SymbolicPlan) -> PreparedPlan:
        """Prepare a plan; the engine owns its own node table at run time."""
        self.registry.apply_imports(plan.imported_namespaces)
        self.registry.reset_runtime_state()
        return PreparedPlan(plan=plan, strategy_name=self.name)

    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None) -> ExecutionResult:
        """Submit goals, evaluate in parallel, then run their side effects."""
        started = time.time()
        plan = prepared.plan
        engine = ComputationEngine(registry=self.registry, backend=self.results_database,
                                   max_concurrency=self.threads, progress=True, debug=self.debug)
        engine.adopt_plan(plan)

        target = plan.goals if goals is None else [g for g in plan.goals if g.id in set(goals)]
        failures: dict[NodeId, str] = {}

        async def evaluate() -> dict[NodeId, Any]:
            queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL)) for g in target]
            await engine.run()
            values: dict[NodeId, Any] = {}
            for goal, query in queries:
                try:
                    values[goal.id] = await query.result()
                except Exception as exc:  # noqa: BLE001
                    failures[goal.id] = repr(exc)
            return values

        values = asyncio.run(evaluate())

        if goals is None:
            for goal in target:
                if goal.id in values:
                    self._side_effect(goal.operation, goal.name, values[goal.id])

        return ExecutionResult(
            success=not failures,
            completed_operations=set(engine.table.completed),
            failed_operations=failures,
            execution_time=time.time() - started,
            total_operations=len(engine.table.nodes),
        )

    # ── Goal side effects ─────────────────────────────────────────────────────────────────────

    def _side_effect(self, operation: str, name: str, value: Any) -> None:
        """Apply a goal's print/save effect to its materialized value."""
        if operation == "print":
            print(f"{name}={self._materialize(value)}")
        elif operation == "save":
            self._save(name, self._materialize(value))
        elif operation == "value":
            pass
        else:
            raise ValueError(f"Unknown goal operation: {operation}")

    def _materialize(self, value: Any) -> Any:
        """Expand a sequence artifact into a concrete list for output."""
        if isinstance(value, SequenceValue):
            return list(value.iter_values())
        return value

    def _save(self, filename: str, value: Any) -> None:
        """Write a goal value to disk by extension."""
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix == ".json":
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        elif suffix in {".pkl", ".pickle", ".bin"}:
            path.write_bytes(pickle.dumps(value))
        else:
            path.write_text(str(value), encoding="utf-8")
