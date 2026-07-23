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

from voxlogica.arrays import PolyArray
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

    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None,
            profile: str | None = None) -> ExecutionResult:
        """Submit goals, evaluate in parallel, then run their side effects.

        ``profile``: ``None`` (default) profiles nothing. Any other string
        wraps the whole run in ``cProfile`` — empty string prints top-30
        cumulative + top-30 tottime to stderr; a non-empty string is a path
        to dump raw ``.pstats`` to (load with ``pstats.Stats(path)`` or
        ``snakeviz path``). This is a real profile of a REAL program, not a
        synthetic benchmark — see ``tests/perf/bench_scheduler.py --profile``
        for that. Added after profiling a real TACAS'19 BraTS case by hand
        found the actual bottleneck (percentiles' sort, not fusion/scheduler
        overhead — see HANDOVER.md §0b/§0c) revealed there was no standard,
        repeatable way to do this against a real .imgql program.
        """
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

        if profile is None:
            values = asyncio.run(evaluate())
        else:
            import cProfile
            import pstats
            import sys as _sys
            prof = cProfile.Profile()
            prof.enable()
            values = asyncio.run(evaluate())
            prof.disable()
            if profile:
                prof.dump_stats(profile)
                print(f"[profile] wrote {profile} — load with pstats.Stats(path) or snakeviz",
                      file=_sys.stderr)
            else:
                stats = pstats.Stats(prof, stream=_sys.stderr)
                stats.sort_stats("cumulative")
                print("\n== profile: cumulative, top 30 ==", file=_sys.stderr)
                stats.print_stats(30)
                stats.sort_stats("tottime")
                print("\n== profile: tottime, top 30 ==", file=_sys.stderr)
                stats.print_stats(30)

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
            cache_summary=engine.metrics(),
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
        """Turn an engine value into its user-facing form for print/save/return.

        This is the sole boundary where values leave the engine to a non-kernel
        consumer, and the mirror of ``executor._wrap``: image values live in the
        node table as ``PolyArray`` (see engine/executor.py) but every caller
        here — print formatting, goal save, the returned result dict — expects
        the native ``sitk.Image`` the pre-fusion engine produced, so unwrap it.
        Sequence artifacts are expanded to a concrete list as before.
        """
        if isinstance(value, PolyArray):
            return value.sitk()
        if isinstance(value, SequenceValue):
            return [item.sitk() if isinstance(item, PolyArray) else item
                    for item in value.iter_values()]
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
