"""Deterministic strict interpreter for SymbolicPlan."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import inspect
import json
import pickle
import time

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import (
    ExecutionResult,
    PageResult,
    PreparedPlan,
    SequenceValue,
)
from voxlogica.lazy.ir import NodeId, NodeSpec, SymbolicPlan
from voxlogica.parser import EBool, ECall, EFor, ELet, ENumber, EString, Expression, parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import DefinitionStore, MaterializationStore, ResultsDatabase


@dataclass
class RuntimeFunction:
    """Runtime function value used while evaluating closures."""

    parameters: list[str]
    expression: Expression
    captures: dict[str, Any]
    evaluator: "StrictExecutionStrategy"

    def invoke(self, args: list[Any]) -> Any:
        if len(args) != len(self.parameters):
            raise ValueError(
                f"Function expects {len(self.parameters)} args, got {len(args)}"
            )
        env = dict(self.captures)
        for parameter, value in zip(self.parameters, args, strict=True):
            env[parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.expression, env)


@dataclass
class RuntimeClosure:
    """Callable runtime closure generated from symbolic closure nodes."""

    parameter: str
    body_expression: Expression
    captures: dict[str, Any]
    evaluator: "StrictExecutionStrategy"

    def apply(self, value: Any) -> Any:
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)

    def __call__(self, value: Any) -> Any:
        return self.apply(value)


class StrictExecutionStrategy(ExecutionStrategy):
    """Strategy that evaluates the symbolic graph with local strict semantics."""

    name = "strict"

    def __init__(
        self,
        registry: PrimitiveRegistry | None = None,
        results_database: ResultsDatabase | None = None,
    ):
        self.registry = registry or PrimitiveRegistry()
        self.results_database = results_database

    def compile(self, plan: SymbolicPlan) -> PreparedPlan:
        self.registry.apply_imports(plan.imported_namespaces)
        return PreparedPlan(
            plan=plan,
            definition_store=DefinitionStore(plan.nodes),
            materialization_store=MaterializationStore(
                backend=self.results_database,
                read_through=False,
                write_through=True,
            ),
            strategy_name=self.name,
        )

    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None) -> ExecutionResult:
        start = time.time()
        failures: dict[NodeId, str] = {}

        if goals is None:
            target_goals = [goal.id for goal in prepared.plan.goals]
        else:
            target_goals = list(goals)

        for goal_id in target_goals:
            try:
                self._evaluate_node(prepared, goal_id)
            except Exception as exc:  # noqa: BLE001
                failures[goal_id] = str(exc)

        if goals is None:
            for goal in prepared.plan.goals:
                if goal.id not in target_goals:
                    continue
                try:
                    value = self._evaluate_node(prepared, goal.id)
                    self._run_goal_side_effect(goal.operation, goal.name, value)
                except Exception as exc:  # noqa: BLE001
                    failures[goal.id] = str(exc)

        duration = time.time() - start
        return ExecutionResult(
            success=(len(failures) == 0),
            completed_operations=prepared.materialization_store.completed_nodes,
            failed_operations=failures,
            execution_time=duration,
            total_operations=len(prepared.plan.nodes),
        )

    def stream(self, prepared: PreparedPlan, node: NodeId, chunk_size: int) -> Iterable[list[object]]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")

        value = self._evaluate_node(prepared, node)
        sequence = self._coerce_sequence(value)

        chunk: list[object] = []
        for item in sequence.iter_values():
            chunk.append(item)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []

        if chunk:
            yield chunk

    def page(
        self,
        prepared: PreparedPlan,
        node: NodeId,
        offset: int,
        limit: int,
    ) -> PageResult:
        value = self._evaluate_node(prepared, node)
        try:
            sequence = self._coerce_sequence(value)
            items = sequence.page(offset=offset, limit=limit)
            next_offset = offset + len(items)
            if len(items) < limit:
                next_offset = None
        except ValueError:
            if offset > 0 or limit <= 0:
                items = []
            else:
                items = [value]
            next_offset = None
        return PageResult(items=items, offset=offset, limit=limit, next_offset=next_offset)

    def _evaluate_node(self, prepared: PreparedPlan, node_id: NodeId) -> Any:
        if prepared.materialization_store.has(node_id):
            return prepared.materialization_store.get(node_id)

        node = prepared.definition_store.get(node_id)

        try:
            if node.kind == "constant":
                value = node.attrs.get("value")
            elif node.kind == "closure":
                value = self._build_runtime_closure(prepared, node)
            elif node.kind == "primitive":
                value = self._execute_primitive(prepared, node)
            else:
                raise ValueError(f"Unsupported node kind: {node.kind}")

            prepared.materialization_store.put(node_id, value)
            return value

        except Exception as exc:  # noqa: BLE001
            prepared.materialization_store.fail(node_id, str(exc))
            raise

    def _build_runtime_closure(self, prepared: PreparedPlan, node: NodeSpec) -> RuntimeClosure:
        body = parse_expression_content(str(node.attrs.get("body", "")))
        parameter = str(node.attrs.get("parameter", "arg"))
        capture_names = list(node.attrs.get("capture_names", []))

        captures = {
            name: self._evaluate_node(prepared, node_id)
            for name, node_id in zip(capture_names, node.args, strict=True)
        }

        function_captures = dict(node.attrs.get("function_captures", {}))
        for name, func_spec in function_captures.items():
            captures[name] = self._build_runtime_function(prepared, func_spec)

        return RuntimeClosure(
            parameter=parameter,
            body_expression=body,
            captures=captures,
            evaluator=self,
        )

    def _build_runtime_function(self, prepared: PreparedPlan, spec: dict[str, Any]) -> RuntimeFunction:
        expression = parse_expression_content(str(spec["body"]))
        captures = {
            name: self._evaluate_node(prepared, node_id)
            for name, node_id in dict(spec.get("captures", {})).items()
        }

        nested = dict(spec.get("functions", {}))
        for name, nested_spec in nested.items():
            captures[name] = self._build_runtime_function(prepared, nested_spec)

        return RuntimeFunction(
            parameters=list(spec.get("parameters", [])),
            expression=expression,
            captures=captures,
            evaluator=self,
        )

    def _execute_primitive(self, prepared: PreparedPlan, node: NodeSpec) -> Any:
        operator = self._normalize_operator(node.operator)

        args = [self._evaluate_node(prepared, arg_id) for arg_id in node.args]
        kwargs = {
            key: self._evaluate_node(prepared, value_id)
            for key, value_id in node.kwargs
        }

        if operator in {"map", "for_loop"}:
            return self._evaluate_map(args, kwargs)

        if operator == "range":
            return self._evaluate_range(args, kwargs)

        if operator == "load":
            return self._evaluate_load(args, kwargs)

        kernel = self.registry.load_kernel(node.operator)
        return self._invoke_kernel(kernel, args, kwargs)

    def _evaluate_map(self, args: list[Any], kwargs: dict[str, Any]) -> SequenceValue:
        if not args:
            raise ValueError("map/for_loop requires a sequence argument")

        sequence = self._coerce_sequence(args[0])
        closure = kwargs.get("closure")
        if closure is None and len(args) > 1:
            closure = args[1]
        if closure is None:
            raise ValueError("map/for_loop requires closure argument")

        total_size = sequence.total_size

        def iterator_factory() -> Iterable[Any]:
            for item in sequence.iter_values():
                if hasattr(closure, "apply") and callable(closure.apply):
                    yield closure.apply(item)
                elif callable(closure):
                    yield closure(item)
                else:
                    raise ValueError("map closure is not callable")

        return SequenceValue(iterator_factory, total_size=total_size)

    def _evaluate_range(self, args: list[Any], kwargs: dict[str, Any]) -> SequenceValue:
        if not args:
            raise ValueError("range requires at least one argument")

        if len(args) == 1:
            start = 0
            stop = int(args[0])
        else:
            start = int(args[0])
            stop = int(args[1])

        size = max(0, stop - start)
        return SequenceValue(lambda: iter(range(start, stop)), total_size=size)

    def _evaluate_load(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if not args:
            raise ValueError("load requires one dataset argument")

        source = args[0]
        if isinstance(source, SequenceValue):
            return source

        if isinstance(source, (list, tuple, range)):
            return SequenceValue(lambda: iter(source), total_size=len(source))

        path = Path(str(source))
        if not path.exists():
            raise ValueError(f"load source not found: {path}")

        suffix = path.suffix.lower()
        if suffix in {".txt", ".csv"}:
            def line_iterator() -> Iterable[str]:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        yield line.rstrip("\n")
            return SequenceValue(line_iterator, total_size=None)

        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return SequenceValue(lambda: iter(payload), total_size=len(payload))
            return payload

        return path.read_bytes()

    def _evaluate_runtime_expression(self, expression: Expression, env: dict[str, Any]) -> Any:
        if isinstance(expression, ENumber):
            return expression.value

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

            function_value = env.get(expression.identifier)
            if isinstance(function_value, RuntimeFunction):
                return function_value.invoke(arg_values)

            operator = self._normalize_operator(expression.identifier)
            if operator in {"map", "for_loop"} and len(arg_values) == 2:
                return self._evaluate_map(arg_values, {})
            if operator == "range":
                return self._evaluate_range(arg_values, {})
            if operator == "load":
                return self._evaluate_load(arg_values, {})

            kernel = self.registry.load_kernel(expression.identifier)
            return self._invoke_kernel(kernel, arg_values, {})

        if isinstance(expression, ELet):
            value = self._evaluate_runtime_expression(expression.value, env)
            new_env = dict(env)
            new_env[expression.variable] = value
            return self._evaluate_runtime_expression(expression.body, new_env)

        if isinstance(expression, EFor):
            sequence = self._coerce_sequence(
                self._evaluate_runtime_expression(expression.iterable, env)
            )

            def iterator_factory() -> Iterable[Any]:
                for item in sequence.iter_values():
                    scoped = dict(env)
                    scoped[expression.variable] = item
                    yield self._evaluate_runtime_expression(expression.body, scoped)

            return SequenceValue(iterator_factory, total_size=sequence.total_size)

        raise ValueError(f"Unsupported runtime expression: {type(expression).__name__}")

    def _coerce_sequence(self, value: Any) -> SequenceValue:
        if isinstance(value, SequenceValue):
            return value

        if isinstance(value, (list, tuple, range)):
            return SequenceValue(lambda: iter(value), total_size=len(value))

        if hasattr(value, "compute") and callable(value.compute):
            computed = value.compute()
            if isinstance(computed, list):
                return SequenceValue(lambda: iter(computed), total_size=len(computed))
            return SequenceValue(lambda: iter(computed), total_size=None)

        if hasattr(value, "__iter__"):
            return SequenceValue(lambda: iter(value), total_size=None)

        raise ValueError(f"Value is not a sequence: {type(value).__name__}")

    def _invoke_kernel(self, kernel, args: list[Any], kwargs: dict[str, Any]) -> Any:
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
                raise ValueError(
                    f"Kernel received too many positional arguments: {len(args)}"
                )
            param = params[index]
            if param.name not in bound_kwargs:
                bound_kwargs[param.name] = value

        return kernel(**bound_kwargs)

    def _run_goal_side_effect(self, operation: str, name: str, value: Any) -> None:
        if operation == "print":
            rendered_value = value
            if isinstance(value, SequenceValue):
                rendered_value = list(value.iter_values())
            print(f"{name}={rendered_value}")
            return

        if operation == "save":
            self._save_to_file(name, value)
            return

        raise ValueError(f"Unknown goal operation: {operation}")

    def _save_to_file(self, filename: str, value: Any) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(value, SequenceValue):
            value = list(value.iter_values())

        suffix = path.suffix.lower()
        if suffix == ".json":
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
            return

        if suffix in {".pkl", ".pickle", ".bin"}:
            path.write_bytes(pickle.dumps(value))
            return

        path.write_text(str(value), encoding="utf-8")

    def _normalize_operator(self, operator: str) -> str:
        if "." in operator:
            return operator.rsplit(".", 1)[1].lower()
        return operator.lower()
