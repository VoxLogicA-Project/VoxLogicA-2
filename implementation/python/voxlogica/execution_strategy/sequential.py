"""Sequential in-process interpreter for symbolic plans.

This strategy evaluates the DAG directly in Python, memoizing node results in
the prepared plan and reconstructing reducer-generated closures on demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import inspect
import json
import pickle
import time

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.lazy.ir import NodeId, NodeSpec, SymbolicPlan
from voxlogica.parser import EArray, EBool, ECall, EFor, ELet, ENumber, ESlice, EString, Expression, parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import DefinitionStore, MaterializationStore, ResultsDatabase


@dataclass
class RuntimeFunction:
    """Reducer-level function value reified for runtime invocation."""

    parameters: list[str]
    expression: Expression
    captures: dict[str, Any]
    evaluator: "SequentialExecutionStrategy"

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
    evaluator: "SequentialExecutionStrategy"

    def apply(self, value: Any) -> Any:
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)

    def __call__(self, value: Any) -> Any:
        return self.apply(value)


class SequentialExecutionStrategy(ExecutionStrategy):
    """Strategy that evaluates the symbolic graph locally."""

    name = "sequential"

    def __init__(self, registry: PrimitiveRegistry | None = None, results_database: ResultsDatabase | None = None):
        self.registry = registry or PrimitiveRegistry()
        self.results_database = results_database
        self._cache_summary: dict[str, Any] = {}
        self._node_events: list[dict[str, Any]] = []

    def compile(self, plan: SymbolicPlan) -> PreparedPlan:
        """Prepare a plan for execution and reset namespace runtime state."""
        self.registry.apply_imports(plan.imported_namespaces)
        self.registry.reset_runtime_state()
        if self.results_database is not None:
            self.results_database.put_plan_definitions(plan)
        return PreparedPlan(
            plan=plan,
            definition_store=DefinitionStore(plan.nodes),
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

        for node_id, node in prepared.plan.nodes.items():
            try: 
                if node_id in prepared.values:
                    continue
                if prepared.materialization_store is not None and prepared.materialization_store.has(node_id):
                    value = prepared.materialization_store.get(node_id)
                    prepared.values[node_id] = value
                    prepared.completed_nodes.add(node_id)
                    source = str(prepared.materialization_store.metadata(node_id).get("source", "runtime-local"))
                    if source == "results-db":
                        self._cache_summary["cached_store"] = int(self._cache_summary.get("cached_store", 0)) + 1
                    else:
                        self._cache_summary["cached_local"] = int(self._cache_summary.get("cached_local", 0)) + 1
                    self._node_events.append({"node_id": node_id, "status": "cached", "cache_source": source})
                    continue
                value = self._evaluate_node_sequential(prepared, node)
                prepared.values[node_id] = value
                prepared.completed_nodes.add(node_id)
                if prepared.materialization_store is not None:
                    prepared.materialization_store.put(node_id, value, metadata={"source": "runtime", "operator": node.operator})
                self._cache_summary["computed"] = int(self._cache_summary.get("computed", 0)) + 1
                self._node_events.append({"node_id": node_id, "status": "computed", "cache_source": "runtime"})
            except Exception as exc:  # noqa: BLE001
                prepared.failures[node_id] = str(exc)
                failures[node_id] = str(exc)
                if prepared.materialization_store is not None:
                    prepared.materialization_store.fail(node_id, str(exc))
                self._cache_summary["failed"] = int(self._cache_summary.get("failed", 0)) + 1
                self._node_events.append({"node_id": node_id, "status": "failed", "error": str(exc)})

        if goals is None:
            for goal in prepared.plan.goals:
                if goal.id not in target_goal_set:
                    continue
                try:
                    value = prepared.values[goal.id]
                    self._run_goal_side_effect(goal.operation, goal.name, value)
                except Exception as exc:  # noqa: BLE001
                    failures[goal.id] = str(exc)

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

    def _evaluate_node_sequential(self, prepared: PreparedPlan, node: NodeSpec) -> Any:
        if node.kind == "constant":
            return node.attrs.get("value")

        if node.kind == "closure":
            return self._build_runtime_closure_from_values(prepared, node)

        if node.kind == "primitive":
            kernel = self.registry.load_kernel(node.operator)
            args = [prepared.values[arg_id] for arg_id in node.args]
            kwargs = {key: prepared.values[value_id] for key, value_id in node.kwargs}
            return self._invoke_kernel(kernel, args, kwargs)

        raise ValueError(f"Unsupported node kind: {node.kind}")


    def _build_runtime_closure_from_values(self, prepared: PreparedPlan, node: NodeSpec) -> RuntimeClosure:
        body = parse_expression_content(str(node.attrs.get("body", "")))
        parameter = str(node.attrs.get("parameter", "arg"))
        capture_names = list(node.attrs.get("capture_names", []))

        captures = {
            name: prepared.values[node_id]
            for name, node_id in zip(capture_names, node.args, strict=True)
        }

        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            captures[name] = self._build_runtime_function_from_values(prepared, spec)

        return RuntimeClosure(parameter=parameter, body_expression=body, captures=captures, evaluator=self)


    def _build_runtime_function_from_values(self, prepared: PreparedPlan, spec: dict[str, Any]) -> RuntimeFunction:
        expression = parse_expression_content(str(spec["body"]))
        captures = {
            name: prepared.values[node_id]
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
        return self._invoke_kernel(kernel, args, kwargs)

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
        raise ValueError(f"Unsupported runtime expression: {type(expression).__name__}")

    def _coerce_sequence(self, value: Any) -> SequenceValue:
        if isinstance(value, SequenceValue):
            return value
        if isinstance(value, (list, tuple, range)):
            return SequenceValue.from_iterable(value)
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray, dict)):
            return SequenceValue.from_iterable(value)
        raise ValueError(f"Value is not a sequence: {type(value).__name__}")

    def _invoke_kernel(self, kernel, args: list[Any], kwargs: dict[str, Any]) -> Any:
        """Adapt engine arguments to the kernel's declared Python signature."""
        signature = inspect.signature(kernel)
        params = list(signature.parameters.values())
        has_varkw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)
        has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params)

        if has_varkw:
            payload = {str(index): value for index, value in enumerate(args)}
            payload.update(kwargs)
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
