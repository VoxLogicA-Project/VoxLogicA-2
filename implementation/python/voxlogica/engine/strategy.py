"""Adapter exposing the computation engine as an execution strategy.

Lets the existing CLI/facade drive the engine through the same
``compile``/``run`` surface as the other strategies: a one-shot ``run`` submits
every goal of the plan, evaluates them in parallel, then applies their
print/save side effects from the materialized results.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import pickle
import signal
import sys
import time
from pathlib import Path
from typing import Any

from voxlogica.arrays import PolyArray
from voxlogica.handles import resolve_deep
from voxlogica.diagnostics.classify import build_report
from voxlogica.diagnostics.store import store_report
from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.priority import Priority
from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import (
    ExecutionResult, PageResult, PreparedPlan, SequenceValue,
)
from voxlogica.lazy.ir import NodeId, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import StorageBackend


def _unresolved_goal_context(engine: ComputationEngine, goal: Any) -> dict[str, str]:
    """Everything known about a goal the scheduler left without a value.

    An unresolved goal is a scheduling failure, and the message alone cannot be
    acted on: it names the goal, but the goal is usually only the victim. The
    node that actually stalled is somewhere in its dependency cone, and by the
    time the run ends the frontier is empty and that information is gone unless
    it is captured here.

    So this walks the cone to the FIRST incomplete node whose own dependencies
    are all complete -- the node that was ready, or should have been, and never
    ran -- and reports it alongside the engine's terminal counters. Everything
    is wrapped: a diagnostic helper that raises would replace the failure being
    reported with its own, which is how a scheduling bug becomes unreproducible.
    """
    ctx: dict[str, str] = {}

    def note(key: str, produce) -> None:
        try:
            value = produce()
        except Exception as exc:  # noqa: BLE001 - never sink the report
            value = f"<unavailable: {type(exc).__name__}: {exc}>"
        ctx[key] = str(value)

    # Even these two reads go through the guard. A scheduling failure can leave
    # the engine in a state where plain attribute access raises, and a helper
    # that dies here would erase the very report it exists to produce.
    try:
        graph = engine.graph
        table = graph.table
    except Exception as exc:  # noqa: BLE001
        ctx["engine_state"] = f"<unreadable: {type(exc).__name__}: {exc}>"
        note("goal_id", lambda: goal.id)
        note("goal_name", lambda: getattr(goal, "name", None))
        return ctx

    note("goal_id", lambda: goal.id)
    note("goal_name", lambda: getattr(goal, "name", None))
    note("goal_operation", lambda: getattr(goal, "operation", None))
    note("goal_completed", lambda: goal.id in table.completed)
    note("goal_has_value", lambda: table.has_value(goal.id))
    note("goal_persisted", lambda: table.persisted(goal.id))
    note("goal_protected", lambda: goal.id in graph.protected)
    note("goal_on_frontier", lambda: goal.id in graph.incomplete)
    note("goal_unmet_deps", lambda: graph.pending.get(goal.id))

    # The stall itself: nearest incomplete ancestor with every dependency done.
    def find_stalled() -> str:
        seen: set = set()
        stack = [goal.id]
        stalled: list[str] = []
        scanned = 0
        while stack and scanned < 100000:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            scanned += 1
            if nid in table.completed:
                continue
            deps = graph.deps(nid)
            unmet = [d for d in deps if d not in table.completed]
            if not unmet:
                spec = table.nodes.get(nid)
                stalled.append(
                    f"{nid[:12]} op={getattr(spec, 'operator', '?')} "
                    f"deps={len(deps)} pending={graph.pending.get(nid)} "
                    f"value={table.has_value(nid)} persisted={table.persisted(nid)}"
                )
                if len(stalled) >= 5:
                    break
            else:
                stack.extend(unmet)
        ctx["unresolved_cone_size"] = str(len(seen))
        return " | ".join(stalled) if stalled else "<none: every ancestor has unmet deps>"

    note("stalled_nodes", find_stalled)

    # Terminal engine state. `in_flight == 0 and ready == 0` with a goal still
    # open means the engine drained rather than wedged -- a different bug from
    # a deadlock, and the counters are the only way to tell them apart later.
    note("engine_snapshot", lambda: json.dumps(
        {k: v for k, v in engine._memory_snapshot().items()
         if k not in ("resident_by_op", "census", "bandwidth")}, default=str))
    note("resident_by_op", lambda: json.dumps(engine.table.resident_by_operator(), default=str))
    note("registered_total", lambda: graph.registered_total)
    note("frontier_size", lambda: len(graph.incomplete))
    note("completed_total", lambda: len(table.completed))
    return ctx


class EngineExecutionStrategy(ExecutionStrategy):
    """Evaluates a plan as a batch of engine queries (one per goal).

    Subclasses the contract on purpose. It did not, and so shipped without
    `page` or `stream` -- an `AttributeError` from a public method, reached
    through an `ExecutionEngine` that had accepted a strategy name and thrown
    it away. The ABC turns that into an error at construction.
    """

    name = "engine"

    def __init__(self, registry: PrimitiveRegistry | None = None, results_database: StorageBackend | None = None,
                 threads: int = 0, debug: bool = False, threads_auto: str = "balanced",
                 observe=None, sparse_cache: bool = False):
        self.registry = registry or PrimitiveRegistry()
        self._serializer_cache: dict[str, dict] | None = None
        self.results_database = results_database
        self.threads = threads
        self.debug = debug
        self.threads_auto = threads_auto
        # Passed straight through to the engine: see ComputationEngine._report.
        self.observe = observe
        # Do not write values that are already dead (see AsyncPersister).
        self.sparse_cache = sparse_cache

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
                                   max_concurrency=self.threads, progress=True, debug=self.debug,
                                   threads_auto=self.threads_auto, observe=self.observe,
                                   sparse_cache=self.sparse_cache)
        engine.adopt_plan(plan)

        target = plan.goals if goals is None else [g for g in plan.goals if g.id in set(goals)]
        failures: dict[NodeId, str] = {}
        diagnostics = []

        def record_failure(exc: BaseException, *, fallback_node_id: NodeId | None = None,
                           context: dict[str, str] | None = None) -> None:
            """Turn every terminal engine/query failure into a CLI diagnostic."""
            node_id = getattr(exc, "node_id", None) or fallback_node_id
            locations = plan.provenance.get(node_id, ()) if node_id else ()
            report = build_report(exc, locations=locations, source_text=plan.source_text)
            diagnostic = replace(report.diagnostic, details_id=store_report(report))
            if context:
                diagnostic = replace(diagnostic,
                                     safe_context={**diagnostic.safe_context, **context})
            diagnostics.append(diagnostic)
            failures[node_id or "<engine>"] = diagnostic.message

        async def evaluate() -> tuple[dict[NodeId, Any], BaseException | None]:
            # Goals are submitted in DECLARATION ORDER with strictly decreasing
            # priority, so the scheduler prefers to FINISH an earlier goal over
            # opening a later one. Equal priority (the old rule) gave it no such
            # reason: on a 369-case sweep the workers spread across 369 goals at
            # once, and a goal's per-case working set stays resident until that
            # goal completes, so peak memory tracks the number of cases OPEN
            # rather than the number in flight.
            #
            # HONESTY NOTE: the runaway memory that motivated this was later
            # root-caused to four value LEAKS (fused-cone interiors, recompute
            # scaffolding, discarded pinned candidates, non-atomic payloads), all
            # fixed separately. This ordering was never isolated as a benefit on
            # its own, and the benchmark that reproduces the manuscript's numbers
            # is single-goal, so it does not exercise this path. It is kept
            # because finishing before opening is the right default for a
            # memory-bounded scheduler, not because it was measured to help.
            #
            # A shared subexpression still inherits
            # the HIGHEST priority of the goals demanding it (see
            # ComputationEngine._schedule_subgraph), so cross-case sharing is
            # unaffected; and the ready queue always keeps every worker fed from
            # later goals whenever an earlier one cannot fill the machine alone.
            # The rank rides BELOW the enum level (level * 1000 + rank) so it
            # orders goals within a priority class without ever promoting one
            # past a genuinely higher class, and stays positive — the engine
            # folds priorities with ``max(self._priority.get(nid, 0), priority)``,
            # which would flatten negative ranks to a single value and undo the
            # ordering entirely.
            ranked = list(enumerate(target))
            queries = [(g, engine.submit(g.id, g.operation, g.name,
                                         int(Priority.NORMAL) * 1000 + (len(ranked) - index)))
                       for index, g in ranked]
            run_error: BaseException | None = None
            try:
                await engine.run()
            except Exception as exc:  # converted below into a structured result
                run_error = exc
            values: dict[NodeId, Any] = {}
            for goal, query in queries:
                # ``ready.wait_idle`` counts admitted work.  A malformed or
                # prematurely-pruned graph can therefore drain while a goal is
                # still unresolved; never await that query forever or exit
                # without explaining the failed run.
                if not query._done.is_set():
                    if run_error is not None:
                        # The engine failure below is the root cause; the
                        # unresolved query is only its consequence.
                        continue
                    record_failure(
                        RuntimeError(
                            "engine finished with an unresolved goal; this is "
                            "a scheduling failure, not a successful empty result"
                        ),
                        fallback_node_id=goal.id,
                        context=_unresolved_goal_context(engine, goal),
                    )
                    continue
                try:
                    values[goal.id] = await query.result()
                except Exception as exc:  # noqa: BLE001
                    record_failure(exc, fallback_node_id=goal.id)
            return values, run_error

        if profile is None:
            values, run_error = asyncio.run(evaluate())
        else:
            print(
                "[profile] WARNING: cProfile's single global call-stack has no "
                "representation for the engine's genuinely concurrent worker "
                "threads (--threads > 1) -- ncalls/cumtime/tottime can be "
                "arbitrarily wrong (e.g. cumtime exceeding wall-clock time) "
                "rather than merely imprecise. Treat this profile as a lead to "
                "investigate, not a measurement to trust. See "
                "https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/34",
                file=sys.stderr,
            )
            import cProfile
            import pstats
            prof = cProfile.Profile()
            prof.enable()
            try:
                values, run_error = asyncio.run(evaluate())
            finally:
                prof.disable()
                # Always dump stats (even if interrupted), to a temp path if profile is stdout-mode.
                dump_path = profile if profile and profile != "" else "/tmp/voxlogica_profile_last.pstats"
                try:
                    prof.dump_stats(dump_path)
                except Exception as e:
                    print(f"[profile] dump_stats failed: {e}", file=sys.stderr)
                if profile and profile != "":
                    print(f"[profile] wrote {profile} — load with pstats.Stats(path) or snakeviz",
                          file=sys.stderr)
                else:
                    stats = pstats.Stats(prof, stream=sys.stderr)
                    stats.sort_stats("cumulative")
                    print("\n== profile: cumulative, top 30 ==", file=sys.stderr)
                    stats.print_stats(30)
                    stats.sort_stats("tottime")
                    print("\n== profile: tottime, top 30 ==", file=sys.stderr)
                    stats.print_stats(30)

        # This engine was constructed fresh above and is not shared with any
        # other engine instance (the one supported reuse pattern —
        # ``engine.numba_backend = other_engines_backend`` — has no caller in
        # this codebase), so it's always safe and correct to shut its numba
        # compile pool down here, once, now that run() itself no longer does
        # this implicitly (see ComputationEngine.shutdown()'s docstring).
        engine.shutdown()

        if goals is None:
            # THROUGH THE CACHE HIERARCHY, not the live tier. On a warm run the
            # sequence comes back from the store naming elements that were never
            # computed in this process, so `table.values` does not have them and
            # `_rematerialize` is the only answer that is right in both cases:
            # resident, else reload, else rebuild from lineage.
            resolve = engine._resolve_reference
            for goal in target:
                if goal.id in values:
                    self._side_effect(goal.operation, goal.name, values[goal.id], resolve)

        if run_error is not None:
            record_failure(run_error)

        return ExecutionResult(
            success=not failures,
            completed_operations=set(engine.table.completed),
            failed_operations=failures,
            execution_time=time.time() - started,
            total_operations=len(engine.table.nodes),
            cache_summary=engine.metrics(),
            diagnostics=diagnostics,
        )

    # ── Goal side effects ─────────────────────────────────────────────────────────────────────

    # ---- sequence access -------------------------------------------------
    #
    # `page` and `stream` exist so a caller can look at part of a sequence
    # without printing the whole of it. With handle-passing they can also
    # COMPUTE only that part: a lazy sequence's value is a tuple of handles,
    # so slicing it and resolving the slice touches the window and nothing
    # else. That is the whole point of the handle work, exercised by a public
    # method rather than only by the operators.

    def _evaluate(self, prepared: PreparedPlan, node: NodeId) -> tuple[Any, Any]:
        """Compute one node -- goal or not -- and return (value, resolver).

        The resolver stays valid after the engine's pool is shut down: goal
        values are protected and the handles they name are held, which is what
        `run` already relies on to print a goal after `engine.shutdown()`.
        """
        plan = prepared.plan
        if node not in plan.nodes:
            raise KeyError(f"node {node!r} is not in this plan")
        self.registry.apply_imports(plan.imported_namespaces)
        engine = ComputationEngine(registry=self.registry, backend=self.results_database,
                                   max_concurrency=self.threads, progress=False,
                                   debug=self.debug, threads_auto=self.threads_auto,
                                   observe=self.observe, sparse_cache=self.sparse_cache)
        engine.adopt_plan(plan)

        async def evaluate() -> Any:
            query = engine.submit(node)
            await engine.run()
            return await query.result()

        value = asyncio.run(evaluate())
        engine.shutdown()
        return value, engine._resolve_reference

    def _items(self, value: Any) -> list[Any] | None:
        """The sequence's elements WITHOUT resolving them, or None if scalar."""
        if isinstance(value, SequenceValue):
            return list(value.iter_values())
        if isinstance(value, (list, tuple)):
            return list(value)
        return None

    def page(self, prepared: PreparedPlan, node: NodeId, offset: int, limit: int) -> PageResult:
        """One half-open window of a sequence node, resolving only that window."""
        if offset < 0 or limit < 0:
            raise ValueError("offset and limit must be non-negative")
        value, resolve = self._evaluate(prepared, node)
        elements = self._items(value)
        if elements is None:
            # A scalar is a one-item page, as the lazy strategy also reports it.
            items = [self._materialize(value, resolve)] if offset == 0 and limit > 0 else []
            return PageResult(items=items, offset=offset, limit=limit, next_offset=None)
        window = elements[offset:offset + limit]
        items = [self._materialize(item, resolve) for item in window]
        exhausted = offset + len(window) >= len(elements)
        return PageResult(items=items, offset=offset, limit=limit,
                          next_offset=None if exhausted else offset + len(window))

    def stream(self, prepared: PreparedPlan, node: NodeId, chunk_size: int):
        """Yield a sequence node's elements in chunks of `chunk_size`."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        value, resolve = self._evaluate(prepared, node)
        elements = self._items(value)
        if elements is None:
            yield [self._materialize(value, resolve)]
            return
        for start in range(0, len(elements), chunk_size):
            yield [self._materialize(item, resolve)
                   for item in elements[start:start + chunk_size]]

    def _side_effect(self, operation: str, name: str, value: Any, resolve=None) -> None:
        """Apply a goal's print/save effect to its materialized value."""
        if operation == "print":
            print(f"{name}={self._materialize(value, resolve)}")
        elif operation == "save":
            self._save(name, self._materialize(value, resolve))
        elif operation == "value":
            pass
        else:
            raise ValueError(f"Unknown goal operation: {operation}")

    def _materialize(self, value: Any, resolve=None) -> Any:
        """Turn an engine value into its user-facing form for print/save/return.

        This is the sole boundary where values leave the engine to a non-kernel
        consumer, and the mirror of ``executor._wrap``: image values live in the
        node table as ``PolyArray`` (see engine/executor.py) but every caller
        here — print formatting, goal save, the returned result dict — expects
        the native ``sitk.Image`` the pre-fusion engine produced, so unwrap it.
        Sequence artifacts are expanded to a concrete list as before.

        Values have exactly TWO consumers: kernels, which the executor's eager
        adapter serves, and the outside world, which arrives here. Both must
        resolve handles, and adapting only the first printed `@b8893698` where a
        number belonged. A goal is `protected`, and the handles its value names
        are held (see graph.hold_handles), so what they name is still resident.
        """
        if resolve is not None:
            value = resolve_deep(value, resolve)
        if isinstance(value, PolyArray):
            return value.sitk(retain_numpy=False)
        if isinstance(value, SequenceValue):
            return [item.sitk(retain_numpy=False) if isinstance(item, PolyArray) else item
                    for item in value.iter_values()]
        if isinstance(value, list):
            # Explicit stack: nesting depth is the program's to choose, and
            # this file may not recurse on it (see AGENTS.md).
            out: list[Any] = []
            stack: list[tuple[list[Any], int, list[Any]]] = [(value, 0, out)]
            while stack:
                items, index, into = stack[-1]
                if index == len(items):
                    stack.pop()
                    continue
                stack[-1] = (items, index + 1, into)
                item = items[index]
                if isinstance(item, list):
                    nested: list[Any] = []
                    into.append(nested)
                    stack.append((item, 0, nested))
                else:
                    into.append(self._materialize(item))
            return out
        return value

    def _serializers(self) -> dict[str, dict]:
        """Extension -> {type: writer}, contributed by the primitive namespaces.

        The simpleitk namespace has published writers for .nii.gz/.png/.mha and
        the rest since it was written, but nothing ever asked it for them, so
        ``save "x.nii.gz"`` wrote str(volume) into a file named like an image.
        Namespaces are asked generically rather than importing simpleitk here:
        a namespace that adds a format should not require editing this file.
        """
        if self._serializer_cache is None:
            table: dict[str, dict] = {}
            modules = getattr(self.registry, "_namespace_modules", {}) or {}
            for module in modules.values():
                getter = getattr(module, "get_serializers", None)
                if getter is None:
                    continue
                try:
                    for extension, writers in (getter() or {}).items():
                        table.setdefault(str(extension).lower(), {}).update(writers)
                except Exception:  # noqa: BLE001 - a broken namespace must not sink save
                    continue
            self._serializer_cache = table
        return self._serializer_cache

    def _save(self, filename: str, value: Any) -> None:
        """Write a goal value to disk by extension."""
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Compound extensions first: ".nii.gz" is one format, not gzip of ".nii",
        # and Path.suffix alone reports ".gz".
        suffixes = [s.lower() for s in path.suffixes]
        candidates = ["".join(suffixes[-2:]), "".join(suffixes[-1:])] if suffixes else []
        serializers = self._serializers()
        for extension in candidates:
            writers = serializers.get(extension)
            if not writers:
                continue
            # Volumes travel as PolyArray; the writers duck-type on the sitk
            # image API, so hand them the image view rather than the wrapper.
            payload = value
            to_sitk = getattr(value, "sitk", None)
            if callable(to_sitk):
                payload = to_sitk()
            for writer in writers.values():
                try:
                    writer(payload, path)
                    return
                except TypeError:
                    continue  # this writer does not accept this value; try the next
        suffix = path.suffix.lower()
        if suffix == ".json":
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        elif suffix in {".pkl", ".pickle", ".bin"}:
            path.write_bytes(pickle.dumps(value))
        else:
            path.write_text(str(value), encoding="utf-8")
