from __future__ import annotations

import dask.bag as db
import time
from typing import Any
import dask.delayed
import dask.distributed

from voxlogica.execution_strategy.sequential import RuntimeClosure, RuntimeFunction, SequentialExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.parser import (
    EArray,
    EBool,
    ECall,
    EFilter,
    EFold,
    EFor,
    ELet,
    ENumber,
    ESlice,
    EString,
    Expression,
    parse_expression_content,
)
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import StorageBackend
from voxlogica.lazy.ir import NodeId
import traceback


class PickleableRuntimeClosure(RuntimeClosure):
    """Pickleable version of RuntimeClosure for Dask serialization."""

    def __getstate__(self):
        return {
            "parameter": self.parameter,
            "body_expression": self.body_expression,
            "captures": self.captures,
        }

    def __setstate__(self, state):
        self.parameter = state["parameter"]
        self.body_expression = state["body_expression"]
        self.captures = state["captures"]
        self.evaluator = None

    def apply(self, value: Any, registry: PrimitiveRegistry | None = None) -> Any:
        if registry is not None:
            self.evaluator = ParallelExecutionStrategy(registry)
        if self.evaluator is None:
            raise ValueError("Parallel closure requires a primitive registry after deserialization")
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)


class ParallelExecutionStrategy(SequentialExecutionStrategy):
    """Execution strategy that uses Dask to execute plans in parallel across multiple processes."""

    name = "parallel"

    def __init__(
        self,
        registry: PrimitiveRegistry | None = None,
        results_database: ResultsDatabase | None = None,
    ):
        super().__init__(registry=registry, results_database=results_database)

    def run(self, prepared: PreparedPlan, goals: list[NodeId]) -> ExecutionResult:
        start = time.time()
        delayed_results = {self.build(prepared,goal.id, is_goal=True) for goal in prepared.plan.goals}
        computed_results = dask.compute(*delayed_results)
        for goal in prepared.plan.goals:
            try:
                value = computed_results[0]
                self._run_goal_side_effect(goal.operation, goal.name, value)
                success = True
                failed_operations = {}
            except Exception as exc:  # noqa: BLE001
                error_trace = traceback.format_exc()
                success = False
                failed_operations = {goal.id: error_trace for goal in prepared.plan.goals}
        end = time.time()
        execution_time = end - start
        cache_summary = self._cache_summary
        return ExecutionResult(
            success=success,
            completed_operations="dummy",
            failed_operations=failed_operations,
            execution_time=execution_time,
            total_operations=len(prepared.plan.goals),
            cache_summary=cache_summary,
        )

    def build(self, prepared, node, is_goal: bool = False):
        [self.build(prepared, dep) for dep in prepared.plan.nodes[node].args]
        return dask.delayed(self._evaluate_node_sequential)(prepared, prepared.plan.nodes[node]), node

    def _evaluate_node_sequential(self, prepared, node: NodeSpec) -> Any:
        if node.kind == "primitive" and node.operator in {"default.map", "map", "default.for_loop","for_loop"}:
            return self._execute_parallel_map(prepared, node)
        return super()._evaluate_node_sequential(prepared, node)

    def _build_runtime_closure_from_values(self, prepared, node) -> PickleableRuntimeClosure:
        body = parse_expression_content(str(node.attrs.get("body", "")))
        parameter = str(node.attrs.get("parameter", "arg"))
        capture_names = list(node.attrs.get("capture_names", []))

        captures = {
            name: prepared.values[node_id]
            for name, node_id in zip(capture_names, node.args, strict=True)
        }

        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            captures[name] = self._build_runtime_function_from_values(prepared, spec)

        return PickleableRuntimeClosure(
            parameter=parameter,
            body_expression=body,
            captures=captures,
            evaluator=self,
        )

    def _execute_parallel_map(self, prepared, node: NodeSpec) -> list[Any]:
        if len(node.args) < 2:
            raise ValueError("map requires sequence and closure arguments")

        sequence = prepared.values[node.args[0]]
        closure = prepared.values[node.args[1]]
        bag = self._to_bag(sequence)
        return bag.map(closure.apply, registry=self.registry).compute()

    def _to_bag(self, value: Any) -> db.Bag:
        if isinstance(value, db.Bag):
            return value
        if isinstance(value, SequenceValue):
            return db.from_sequence(value.iter_values())
        if isinstance(value, (list, tuple, range)):
            return db.from_sequence(value)
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray, dict)):
            return db.from_sequence(value)
        raise ValueError(f"Value is not a sequence: {type(value).__name__}")

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
