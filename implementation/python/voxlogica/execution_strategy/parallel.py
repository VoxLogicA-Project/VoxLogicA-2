from __future__ import annotations

import dask.bag as db
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.lazy.ir import NodeId, NodeSpec, SymbolicPlan
from voxlogica.parser import EArray, EBool, ECall, EFilter, EFold, EFor, ELet, ENumber, ESlice, EString, Expression, parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import MaterializationStore, ResultsDatabase
from voxlogica.value_model import VOX_FORMAT_VERSION
from voxlogica.execution_strategy.sequential import SequentialExecutionStrategy

@dataclass
class RuntimeFunction:
    """Reducer-level function value reified for runtime invocation."""

    parameters: list[str]
    expression: Expression
    captures: dict[str, Any]
    evaluator: "ParallelExecutionStrategy"

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
    evaluator: "ParallelExecutionStrategy"

    def apply(self, value: Any) -> Any:
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)

    def __call__(self, value: Any) -> Any:
        return self.apply(value)
    
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
        self.evaluator = None  # Will be set by ParallelExecutionStrategy after deserialization

    def apply(self, value: Any, registry: PrimitiveRegistry) -> Any:
        self.evaluator = ParallelExecutionStrategy(registry)
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)


class ParallelExecutionStrategy(SequentialExecutionStrategy):
    """Execution strategy that uses Dask to execute plans in parallel across multiple processes."""

    name = "parallel"
    
    def __init__(self, registry: PrimitiveRegistry | None = None):
        self.registry = registry
        self.results_database = None
        self._cache_summary: dict[str, Any] = {}
        self._node_events: list[dict[str, Any]] = []

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
            registry = self.registry
            # sequence = self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env)).iter_values()
            sequence = db.from_sequence(self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env)).iter_values())
            closure = PickleableRuntimeClosure(
                { 'parameter': expression.variable,
                'body_expression': expression.body,
                'captures': env }
            )
            #if hasattr(closure, "apply") and callable(closure.apply):
            return sequence.map(closure.apply, registry=registry)
            return super()._evaluate_runtime_expression(expression, env)
        if isinstance(expression, EFilter):
            sequence = self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env)).iter_values()
            closure = RuntimeClosure(
                parameter=expression.variable,
                body_expression=expression.predicate,
                captures=env,
                evaluator=self
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