"""Lazy in-process interpreter for symbolic plans.

This strategy evaluates the DAG directly in Python, memoizing node results in
the prepared plan and reconstructing reducer-generated closures on demand.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import inspect
import json
import pickle
import time
import traceback

from tqdm import tqdm

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.lazy.ir import NodeId, NodeSpec, SymbolicPlan
from voxlogica.parser import EArray, EBool, ECall, EFilter, EFold, EFor, ELet, ENumber, ESlice, EString, Expression, parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import MaterializationStore, StorageBackend
from voxlogica.value_model import adapt_runtime_value
from voxlogica.pod_codec import encode_for_storage
from voxlogica.lazy.hash import hash_sequence_item

# Module-level thread pool: workers call ITK kernels (GIL released → true parallelism).
# max_workers=None → Python default: min(32, cpu_count + 4).
_executor = ThreadPoolExecutor(max_workers=None)

_LAZY_SEQUENCE_OPERATORS = {
    "default.map", 
    "map",
    "default.filter",
    "filter",
    "default.for_loop",
    "for_loop"
}

@dataclass
class RuntimeFunction:
    """Reducer-level function value reified for runtime invocation."""

    parameters: list[str]
    expression: Expression
    captures: dict[str, Any]
    evaluator: "LazyExecutionStrategy"

    def invoke(self, args: list[Any]) -> Any:
        if len(args) != len(self.parameters):
            raise ValueError(f"Function expects {len(self.parameters)} args, got {len(args)}")
        env = dict(self.captures)
        for parameter, value in zip(self.parameters, args, strict=False):
            env[parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.expression, env)


@dataclass
class RuntimeClosure:
    """One-argument runtime closure used by ``map`` and ``for_loop``."""

    parameter: str
    body_expression: Expression
    captures: dict[str, Any]
    evaluator: "LazyExecutionStrategy"

    def apply(self, value: Any) -> Any:
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)

    def __call__(self, value: Any) -> Any:
        return self.apply(value)
    
@dataclass
class Demand:
    pass

@dataclass
class FullDemand(Demand):
    pass

@dataclass
class SliceDemand(Demand):
    start = 0
    stop = 0

    def __init__(self,start,stop):
        self.start = start
        self.stop = stop

@dataclass
class IndexDemand(Demand):
    index = -1
    
    def __init__(self,index):
        self.index = index


class LazyExecutionStrategy(ExecutionStrategy):
    """Strategy that evaluates the symbolic graph locally."""

    name = "lazy"

    def __init__(self, registry: PrimitiveRegistry | None = None, results_database: StorageBackend | None = None):
        self.registry = registry or PrimitiveRegistry()
        self.results_database = results_database
        self._cache_summary: dict[str, Any] = {}
        self._node_events: list[dict[str, Any]] = []
        self._progress: tqdm | None = None

    def compile(self, plan: SymbolicPlan) -> PreparedPlan:
        """Prepare a plan for execution and reset namespace runtime state."""
        self.registry.apply_imports(plan.imported_namespaces)
        self.registry.reset_runtime_state()
        #if self.results_database is not None:
        #    self.results_database.put_plan_definitions(plan)
        return PreparedPlan(
            plan=plan,
            # definition_store=DefinitionStore(plan.nodes),
            materialization_store=MaterializationStore(
                backend=self.results_database,
                read_through=True,
                write_through=True,
            ),
            strategy_name=self.name,
        )

    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None) -> ExecutionResult:
        started = time.time()
        failures: dict[NodeId, str] = {}
        self._cache_summary = {"computed": 0, "cached_local": 0, "cached_store": 0, "failed": 0}
        self._node_events = []
        target_goals = [goal.id for goal in prepared.plan.goals] if goals is None else list(goals)
        target_goal_set = set(target_goals)

        self._progress = tqdm(total=len(prepared.plan.nodes), desc="nodes", unit="node",
                              dynamic_ncols=True, file=__import__("sys").stderr, leave=True)
        try:
            asyncio.run(self._async_run(prepared, list(target_goal_set)))
        finally:
            self._progress.close()
            self._progress = None

        if goals is None:
            for goal in prepared.plan.goals:
                if goal.id not in target_goal_set:
                    continue
                if goal.id not in prepared.values:
                    # Goal was not computed - check if it failed
                    if goal.id in failures:
                        # Already recorded as failed, skip side effect
                        continue
                    # This shouldn't happen - goal should be in values or failures
                    import sys
                    print(f"WARNING: Goal {goal.id} not in prepared.values or failures", file=sys.stderr)
                    print(f"Available values: {list(prepared.values.keys())}", file=sys.stderr)
                    print(f"Failed operations: {list(failures.keys())}", file=sys.stderr)
                    continue
                try:
                    value = prepared.values[goal.id]
                    self._run_goal_side_effect(goal.operation, goal.name, value)
                except Exception as exc:  # noqa: BLE001
                    error_trace = traceback.format_exc()
                    failures[goal.id] = error_trace

        if prepared.materialization_store is not None:
            prepared.materialization_store.flush(timeout_s=10.0)

        return ExecutionResult(
            success=not failures,
            completed_operations=set(prepared.completed_nodes),
            failed_operations=failures,
            execution_time=time.time() - started,
            total_operations=len(prepared.plan.nodes),
            cache_summary=dict(self._cache_summary),
            node_events=list(self._node_events),
        )

    def stream(self, prepared: PreparedPlan, node: NodeId, chunk_size: int) -> Iterable[list[object]]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")

        sequence = self._coerce_sequence(self._ensure_node_value(prepared, node))
        chunk: list[object] = []
        for item in sequence.iter_values():
            chunk.append(item)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk

    def page(self, prepared: PreparedPlan, node: NodeId, offset: int, limit: int) -> PageResult:
        value = self._ensure_node_value(prepared, node)
        try:
            sequence = self._coerce_sequence(value)
            items = sequence.page(offset=offset, limit=limit)
            next_offset = offset + len(items)
            if len(items) < limit:
                next_offset = None
        except ValueError:
            items = [] if offset > 0 or limit <= 0 else [value]
            next_offset = None
        return PageResult(items=items, offset=offset, limit=limit, next_offset=next_offset)
    
    def cache(self, prepared: PreparedPlan, nodeId: NodeId, value: Any):
        node = prepared.plan.nodes[nodeId]
        expression = node.operator if node.kind != "closure" else {"body": node.attrs.get("body"), "parameter": node.attrs.get("parameter"), "capture_names": node.attrs.get("capture_names"), "function_captures": node.attrs.get("function_captures")}
        dependencies = list(node.args) + [value_id for _, value_id in node.kwargs]
        if self.results_database is not None:
            self.results_database.put_success(nodeId, value, metadata={"source": "runtime", "operator": node.operator})
        if prepared.materialization_store is not None:
            prepared.materialization_store.put(nodeId, expression, dependencies, value, metadata={"source": "runtime", "operator": node.operator})
        prepared.completed_nodes.add(nodeId)
        if self._progress is not None:
            self._progress.update(1)

    def cache_sequence_item(self, prepared: PreparedPlan, nodeId: NodeId, index:int, value:Any):
        node = prepared.plan.nodes[nodeId]
        id = hash_sequence_item(nodeId,index)
        expression = node.operator if node.kind != "closure" else {"body": node.attrs.get("body"), "parameter": node.attrs.get("parameter"), "capture_names": node.attrs.get("capture_names"), "function_captures": node.attrs.get("function_captures")}
        dependencies = list(node.args) + [value_id for _, value_id in node.kwargs]
        if self.results_database is not None:
            self.results_database.put_success(id, value, metadata={"source": "runtime", "operator": node.operator, "index": id})
        prepared.completed_nodes.add(nodeId)
        if self._progress is not None:
            self._progress.update(1)
        if prepared.materialization_store is not None:
            prepared.materialization_store.put(id, expression, dependencies, value, metadata={"source": "runtime", "operator": node.operator})

    def cache_lookup(self, prepared: PreparedPlan, nodeId: NodeId):
        value = None
        if prepared.materialization_store is not None:
            value = prepared.materialization_store.get(nodeId)
            if value is not None:
                if nodeId not in prepared.completed_nodes:
                    prepared.completed_nodes.add(nodeId)
                    if self._progress is not None:
                        self._progress.update(1)
                return value
        if self.results_database is not None:
            tmp = self.results_database.get_record(nodeId)
            if tmp is not None:
                if tmp.vox_type == "image":
                    value = tmp.payload_bin
                else:
                    value = tmp.payload_json["value"]
        if value is not None and nodeId not in prepared.completed_nodes:
            prepared.completed_nodes.add(nodeId)
            if self._progress is not None:
                self._progress.update(1)
        return value

    def cache_sequence_item_lookup(self, prepared: PreparedPlan, nodeId: NodeId, index: int):
        value = None
        if prepared.materialization_store is not None:
            value = prepared.materialization_store.get(hash_sequence_item(nodeId, index))
            if value is not None:
                return value
        if self.results_database is not None:
            tmp = self.results_database.get_record(hash_sequence_item(nodeId, index))
            if tmp is not None:
                if tmp.vox_type == "image":
                    value = tmp.payload_bin
                else:
                    value = tmp.payload_json["value"]
        return value

    def _evaluate_node_lazy(self, prepared: PreparedPlan, nodeid: NodeId, demand: Demand) -> Any:
        value = self.cache_lookup(prepared, nodeid)
        if value is not None:
            return value
        node = prepared.plan.nodes[nodeid]
        if node.kind == "constant":
            return node.attrs.get("value")

        if node.kind == "closure":
            return self._build_runtime_closure_from_values(prepared, node)

        if node.kind == "primitive":
            if node.operator == "default.subsequence":
                start = int(self._evaluate_node_lazy(prepared, node.args[1], demand))
                stop = int(self._evaluate_node_lazy(prepared, node.args[2], demand))
                child_operator = prepared.plan.nodes[node.args[0]].operator
                if child_operator in _LAZY_SEQUENCE_OPERATORS:
                    value = self._evaluate_node_lazy(
                        prepared, node.args[0], SliceDemand(start, stop)
                    )
                else:
                    sequence = self._evaluate_node_lazy(prepared, node.args[0], demand)
                    kernel = self.registry.load_kernel("default.subsequence")
                    value = self._invoke_kernel(kernel, [sequence, start, stop], {})
                self.cache(prepared, nodeid, value)
                return value
            
            kernel = self.registry.load_kernel(node.operator)
            tmpargs = [self._evaluate_node_lazy(prepared,arg_id,demand) for arg_id in node.args]
            kwargs = {key: self._evaluate_node_lazy(prepared,arg_id,demand) for key, arg_id in node.kwargs}
            if node.operator in _LAZY_SEQUENCE_OPERATORS:
                if isinstance(demand,SliceDemand):
                    value = []
                    for i in range(demand.start,demand.stop):
                        tmp = self.cache_sequence_item_lookup(prepared, nodeid, i)
                        if tmp is not None:
                            value.extend(tmp)
                        else:
                            args = tmpargs + [i,i+1]
                            tmp = self._invoke_kernel(kernel, args, kwargs, node.attrs)
                            value.extend(tmp)
                            self.cache_sequence_item(prepared,nodeid,i,tmp)
                else: 
                    args = tmpargs
                    value = self._invoke_kernel(kernel, args, kwargs, node.attrs)
                    self.cache(prepared,nodeid,value)
            else:
                args = tmpargs
                # kwargs = {key: self._evaluate_node_lazy(prepared,arg_id,demand) for key, arg_id in node.kwargs}
                value = self._invoke_kernel(kernel, args, kwargs, node.attrs)
            if node.operator in _LAZY_SEQUENCE_OPERATORS and isinstance(demand,FullDemand):
                for i in range(0,len(value)):
                    self.cache_sequence_item(prepared,nodeid,i,value[i])
            else:
                self.cache(prepared, nodeid, value)
            return value

        raise ValueError(f"Unsupported node kind: {node.kind}")


    # ── Async task-graph executor (issue #19) ───────────────────────────────────────────────────

    @staticmethod
    def _extract_function_spec_node_ids(spec: dict) -> list[NodeId]:
        """Recursively collect all node IDs referenced inside a function_capture spec."""
        ids = list(dict(spec.get("captures", {})).values())
        for nested in dict(spec.get("functions", {})).values():
            ids.extend(LazyExecutionStrategy._extract_function_spec_node_ids(nested))
        return ids

    def _get_all_deps(self, node: NodeSpec) -> set[NodeId]:
        """All dependency node IDs for a node, including hidden function_capture references."""
        deps: set[NodeId] = set(node.args) | {a for _, a in node.kwargs}
        for spec in node.attrs.get("function_captures", {}).values():
            deps.update(self._extract_function_spec_node_ids(spec))
        return deps

    def _compute_primitive(self, prepared: PreparedPlan, nid: NodeId) -> Any:
        """Evaluate a single primitive node whose dependencies are already in prepared.values.

        Called from a ThreadPoolExecutor thread — must only read from prepared.values
        (never write to it) and must not call cache() or update tqdm.
        """
        node = prepared.plan.nodes[nid]
        assert node.kind == "primitive", f"_compute_primitive called on non-primitive {nid}"

        if node.operator == "default.subsequence":
            start = int(prepared.values[node.args[1]])
            stop  = int(prepared.values[node.args[2]])
            sequence = prepared.values[node.args[0]]
            kernel = self.registry.load_kernel("default.subsequence")
            return self._invoke_kernel(kernel, [sequence, start, stop], {})

        kernel = self.registry.load_kernel(node.operator)
        args   = [prepared.values[arg_id] for arg_id in node.args]
        kwargs = {key: prepared.values[arg_id] for key, arg_id in node.kwargs}
        return self._invoke_kernel(kernel, args, kwargs, node.attrs)

    def _build_runtime_closure_from_values_eager(self, prepared: PreparedPlan, node: NodeSpec) -> RuntimeClosure:
        """Build a RuntimeClosure by reading captures from prepared.values (already evaluated)."""
        body           = parse_expression_content(str(node.attrs.get("body", "")))
        parameter      = str(node.attrs.get("parameter", "arg"))
        capture_names  = list(node.attrs.get("capture_names", []))
        captures = {
            name: prepared.values[node_id]
            for name, node_id in zip(capture_names, node.args, strict=True)
        }
        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            captures[name] = self._build_runtime_function_from_values_eager(prepared, spec)
        return RuntimeClosure(parameter=parameter, body_expression=body, captures=captures, evaluator=self)

    def _build_runtime_function_from_values_eager(self, prepared: PreparedPlan, spec: dict) -> RuntimeFunction:
        """Build a RuntimeFunction by reading captures from prepared.values."""
        expression = parse_expression_content(str(spec["body"]))
        captures = {
            name: prepared.values[node_id]
            for name, node_id in dict(spec.get("captures", {})).items()
        }
        for name, nested_spec in dict(spec.get("functions", {})).items():
            captures[name] = self._build_runtime_function_from_values_eager(prepared, nested_spec)
        return RuntimeFunction(
            parameters=list(spec.get("parameters", [])),
            expression=expression,
            captures=captures,
            evaluator=self,
        )

    async def _async_run(self, prepared: PreparedPlan, goal_ids: list[NodeId]) -> None:
        """Async task-graph executor: evaluate all nodes reachable from goal_ids in parallel.

        Strategy:
          1. BFS from goals to find all reachable nodes.
          2. Pre-populate from cache; exclude already-known nodes.
          3. Build a reverse-edge dependency graph over nodes still needing computation.
          4. Seed with zero-in-degree nodes; when a node finishes, decrement its dependents
             and launch any that become ready.  ITK kernels run in a ThreadPoolExecutor
             (GIL released → true CPU parallelism).  All bookkeeping stays in the event loop.
        """
        # ── Step 1: find reachable nodes ──────────────────────────────────────
        reachable: set[NodeId] = set()
        queue = list(goal_ids)
        while queue:
            nid = queue.pop()
            if nid in reachable:
                continue
            reachable.add(nid)
            for dep in self._get_all_deps(prepared.plan.nodes[nid]):
                if dep not in reachable:
                    queue.append(dep)

        # ── Step 2: pre-populate from cache ───────────────────────────────────
        for nid in list(reachable):
            if nid not in prepared.values:
                value = self.cache_lookup(prepared, nid)
                if value is not None:
                    prepared.values[nid] = value

        # ── Step 3: build dependency graph for uncached nodes ─────────────────
        to_compute: set[NodeId] = {nid for nid in reachable if nid not in prepared.values}

        # dependents[p] = list of children that are waiting on p
        dependents: dict[NodeId, list[NodeId]] = defaultdict(list)
        pending: dict[NodeId, int] = {}  # remaining unsatisfied deps within to_compute

        for nid in to_compute:
            deps_needed = [d for d in self._get_all_deps(prepared.plan.nodes[nid]) if d in to_compute]
            pending[nid] = len(deps_needed)
            for dep in deps_needed:
                dependents[dep].append(nid)

        ready = [nid for nid, count in pending.items() if count == 0]

        if not to_compute:
            return

        # ── Step 4: async execution ───────────────────────────────────────────
        active = len(ready)
        done_event = asyncio.Event()
        if active == 0:
            done_event.set()
            return

        loop = asyncio.get_running_loop()

        async def eval_node(nid: NodeId) -> None:
            nonlocal active
            node = prepared.plan.nodes[nid]

            if node.kind == "constant":
                value = node.attrs.get("value")
            elif node.kind == "closure":
                # Closures are cheap Python objects; build in the event loop.
                value = self._build_runtime_closure_from_values_eager(prepared, node)
            else:
                # Primitive: run in thread pool so ITK can use all cores (GIL released).
                value = await loop.run_in_executor(_executor, self._compute_primitive, prepared, nid)

            # ── Back in event loop — no preemption ────────────────────────────
            prepared.values[nid] = value

            # Cache the result (writes tqdm + store; safe: event-loop thread only).
            if node.kind == "primitive" and node.operator in _LAZY_SEQUENCE_OPERATORS:
                self.cache(prepared, nid, value)
                for i, item in enumerate(value):
                    self.cache_sequence_item(prepared, nid, i, item)
            else:
                self.cache(prepared, nid, value)

            # Launch newly-ready children.
            new_ready: list[NodeId] = []
            for child_id in dependents[nid]:
                pending[child_id] -= 1
                if pending[child_id] == 0:
                    new_ready.append(child_id)

            active += len(new_ready) - 1  # add children, subtract self (no preemption)
            for child_id in new_ready:
                asyncio.create_task(eval_node(child_id))

            if active == 0:
                done_event.set()

        for nid in ready:
            asyncio.create_task(eval_node(nid))

        await done_event.wait()

    # ── End async executor ───────────────────────────────────────────────────────────────────

    def _build_runtime_closure_from_values(self, prepared: PreparedPlan, node: NodeSpec) -> RuntimeClosure:
        body = parse_expression_content(str(node.attrs.get("body", "")))
        parameter = str(node.attrs.get("parameter", "arg"))
        capture_names = list(node.attrs.get("capture_names", []))

        captures = {
            name: self._evaluate_node_lazy(prepared,node_id,FullDemand())
            for name, node_id in zip(capture_names, node.args, strict=True)
        }

        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            captures[name] = self._build_runtime_function_from_values(prepared, spec)

        return RuntimeClosure(parameter=parameter, body_expression=body, captures=captures, evaluator=self)


    def _build_runtime_function_from_values(self, prepared: PreparedPlan, spec: dict[str, Any]) -> RuntimeFunction:
        expression = parse_expression_content(str(spec["body"]))
        captures = {
            name: self._evaluate_node_lazy(prepared,node_id,FullDemand())
            for name, node_id in dict(spec.get("captures", {})).items()
        }
        for name, nested_spec in dict(spec.get("functions", {})).items():
            captures[name] = self._build_runtime_function_from_values(prepared, nested_spec)
        return RuntimeFunction(
            parameters=list(spec.get("parameters", [])),
            expression=expression,
            captures=captures,
            evaluator=self,
        )

    def _execute_primitive(self, prepared: PreparedPlan, node: NodeSpec) -> Any:
        """Load the primitive kernel, materialize inputs, and invoke it."""
        kernel = self.registry.load_kernel(node.operator)
        args = [prepared.values[arg_id] for arg_id in node.args]
        kwargs = {key: prepared.values[value_id] for key, value_id in node.kwargs}
        return self._invoke_kernel(kernel, args, kwargs, node.attrs)

    def _ensure_node_value(self, prepared: PreparedPlan, node_id: NodeId) -> Any:
        if node_id in prepared.values:
            return prepared.values[node_id]
        for current_id, node in prepared.plan.nodes.items():
            if current_id in prepared.values:
                continue
            value = self._evaluate_node_sequential(prepared, node)
            prepared.values[current_id] = value
            prepared.completed_nodes.add(current_id)
            if current_id == node_id:
                return value
        raise KeyError(f"Node not found in prepared plan: {node_id}")

    def _evaluate_runtime_expression(self, expression: Expression, env: dict[str, Any]) -> Any:
        """Evaluate deferred closure bodies using the same primitive runtime."""
        if isinstance(expression, ENumber):
            return expression.value
        if isinstance(expression, EArray):
            return [self._evaluate_runtime_expression(item, env) for item in expression.items]
        if isinstance(expression, EBool):
            return expression.value
        if isinstance(expression, EString):
            return expression.value
        if isinstance(expression, ECall):
            if not expression.arguments:
                if expression.identifier in env:
                    return env[expression.identifier]
                kernel = self.registry.load_kernel(expression.identifier)
                return self._invoke_kernel(kernel, [], {})
            arg_values = [self._evaluate_runtime_expression(arg, env) for arg in expression.arguments]
            # Closure bodies may call captured user functions or globally
            # registered primitives, so resolution checks the environment first.
            function_value = env.get(expression.identifier)
            if isinstance(function_value, RuntimeFunction):
                return function_value.invoke(arg_values)
            kernel = self.registry.load_kernel(expression.identifier)
            return self._invoke_kernel(kernel, arg_values, {})
        if isinstance(expression, ESlice):
            sequence_value = self._evaluate_runtime_expression(expression.sequence, env)
            start_value = self._evaluate_runtime_expression(expression.start, env) if expression.start is not None else None
            stop_value = self._evaluate_runtime_expression(expression.stop, env) if expression.stop is not None else None
            kernel = self.registry.load_kernel("slice")
            return self._invoke_kernel(kernel, [sequence_value, start_value, stop_value], {})
        if isinstance(expression, ELet):
            value = self._evaluate_runtime_expression(expression.value, env)
            next_env = dict(env)
            next_env[expression.variable] = value
            return self._evaluate_runtime_expression(expression.body, next_env)
        if isinstance(expression, EFor):
            sequence = self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env))
            closure = RuntimeClosure(
                parameter=expression.variable,
                body_expression=expression.body,
                captures=env,
                evaluator=self,
            )
            return [closure.apply(item) for item in sequence.iter_values()]
        if isinstance(expression, EFilter):
            sequence = self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env))
            closure = RuntimeClosure(
                parameter=expression.variable,
                body_expression=expression.predicate,
                captures=env,
                evaluator=self,
            )
            return [
                item
                for item in sequence.iter_values()
                if bool(closure.apply(item))
            ]
        if isinstance(expression, EFold):
            from voxlogica.primitives.default.fold import fold_sequence

            sequence = self._evaluate_runtime_expression(expression.sequence, env)
            init = (
                None
                if expression.init is None
                else self._evaluate_runtime_expression(expression.init, env)
            )
            return fold_sequence(expression.operator, init, sequence)
        raise ValueError(f"Unsupported runtime expression: {type(expression).__name__}")

    def _coerce_sequence(self, value: Any) -> SequenceValue:
        if isinstance(value, SequenceValue):
            return value
        if isinstance(value, (list, tuple, range)):
            return SequenceValue.from_iterable(value)
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray, dict)):
            return SequenceValue.from_iterable(value)
        raise ValueError(f"Value is not a sequence: {type(value).__name__}")

    def _invoke_kernel(
        self,
        kernel,
        args: list[Any],
        kwargs: dict[str, Any],
        attrs: dict[str, Any] | None = None,
    ) -> Any:
        """Adapt engine arguments to the kernel's declared Python signature."""
        signature = inspect.signature(kernel)
        params = list(signature.parameters.values())
        has_varkw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)
        has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params)

        if has_varkw:
            payload = {str(index): value for index, value in enumerate(args)}
            payload.update(kwargs)
            if attrs:
                payload.update(attrs)
            return kernel(**payload)
        if has_varargs:
            return kernel(*args, **kwargs)

        bound_kwargs = dict(kwargs)
        for index, value in enumerate(args):
            if index >= len(params):
                raise ValueError(f"Kernel received too many positional arguments: {len(args)}")
            param = params[index]
            if param.name not in bound_kwargs:
                bound_kwargs[param.name] = value
        return kernel(**bound_kwargs)

    def _run_goal_side_effect(self, operation: str, name: str, value: Any) -> None:
        if operation == "print":
            print(f"{name}={self._materialize_goal_value(value)}")
            return
        if operation == "save":
            self._save_to_file(name, self._materialize_goal_value(value))
            return
        raise ValueError(f"Unknown goal operation: {operation}")

    def _save_to_file(self, filename: str, value: Any) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix == ".json":
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
            return
        if suffix in {".pkl", ".pickle", ".bin"}:
            path.write_bytes(pickle.dumps(value))
            return
        path.write_text(str(value), encoding="utf-8")

    def _materialize_goal_value(self, value: Any) -> Any:
        if isinstance(value, SequenceValue):
            return list(value.iter_values())
        return value
