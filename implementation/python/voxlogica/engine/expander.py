"""Dynamic loop expansion — the single semantics for iteration.

Iteration in VoxLogicA is defined exactly once, by reduction (AST -> nodes).
A ``for_loop``/``map`` node is never interpreted to a value; instead, once its
iterable is materialized, its closure body is *reduced* once per element into new
nodes — the same operation the reducer performs at compile time for constant
iterables, now performed at runtime with the concrete element values. Hash-consing
deduplicates subexpressions shared across elements.

The expander never computes values; it only grows the DAG, returning the new node
ids for the scheduler to pick up.
"""

from __future__ import annotations

from typing import Any

from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeId, NodeSpec
from voxlogica.parser import parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry

_EXPANDABLE = {"for_loop", "default.for_loop", "map", "default.map"}


class Expander:
    """Unrolls closure-over-sequence nodes into per-element DAG nodes."""

    def __init__(self, table: NodeTable, registry: PrimitiveRegistry):
        self.table = table
        self.registry = registry

    def can_expand(self, node: NodeSpec) -> bool:
        """True for a closure-over-sequence node (args = (iterable, closure))."""
        return node.kind == "primitive" and node.operator in _EXPANDABLE and len(node.args) == 2

    def expand(self, node_id: NodeId, node: NodeSpec) -> tuple[NodeId, set[NodeId]] | None:
        """Reduce the body per element; return (sequence_id, new_node_ids) or None."""
        from voxlogica.reducer import WorkPlan, OperationVal, reduce_expression, _create_constant_node, _plan_primitive_call

        iterable = self.table.values.get(node.args[0])
        if iterable is None:
            return None
        try:
            items = list(iterable)
        except TypeError:
            return None

        closure = self.table.nodes[node.args[1]]
        if closure.kind != "closure":
            return None
        variable = str(closure.attrs.get("parameter", "arg"))
        body = parse_expression_content(str(closure.attrs.get("body", "")))
        base_env = self._closure_environment(closure)

        plan = WorkPlan(nodes=self.table.nodes, registry=self.registry)
        before = set(self.table.nodes.keys())
        body_ids = []
        for item in items:
            const_id = _create_constant_node(plan, item)
            body_ids.append(reduce_expression(base_env.bind(variable, OperationVal(const_id)), plan, body))
        sequence_id = _plan_primitive_call(plan, "sequence", tuple(body_ids), output_kind="sequence")
        return sequence_id, set(self.table.nodes.keys()) - before

    def _closure_environment(self, closure: NodeSpec) -> Any:
        """Rebuild the closure's reduce-time environment from its captures."""
        from voxlogica.reducer import Environment, OperationVal

        env = Environment({})
        for name, arg_id in zip(closure.attrs.get("capture_names", []), closure.args, strict=False):
            env = env.bind(name, OperationVal(arg_id))
        for name, spec in dict(closure.attrs.get("function_captures", {})).items():
            env = env.bind(name, self._function_value(spec))
        return env

    def _function_value(self, spec: dict) -> Any:
        """Rebuild a captured FunctionVal from its serialized spec."""
        from voxlogica.reducer import Environment, OperationVal, FunctionVal

        env = Environment({})
        for name, node_id in dict(spec.get("captures", {})).items():
            env = env.bind(name, OperationVal(node_id))
        for name, nested in dict(spec.get("functions", {})).items():
            env = env.bind(name, self._function_value(nested))
        return FunctionVal(env, list(spec.get("parameters", [])), parse_expression_content(str(spec["body"])))

    @staticmethod
    def function_capture_ids(attrs: dict) -> set[NodeId]:
        """Node ids referenced inside an attrs' function_captures, recursively."""
        ids: set[NodeId] = set()
        stack = list(attrs.get("function_captures", {}).values())
        while stack:
            spec = stack.pop()
            ids.update(dict(spec.get("captures", {})).values())
            stack.extend(dict(spec.get("functions", {})).values())
        return ids

    @staticmethod
    def dependencies(node: NodeSpec) -> set[NodeId]:
        """Every node id a node depends on, including hidden function-capture refs."""
        deps: set[NodeId] = set(node.args) | {a for _, a in node.kwargs}
        return deps | Expander.function_capture_ids(node.attrs)

    @staticmethod
    def closure_capture_ids(node: NodeSpec) -> set[NodeId]:
        """All node ids a closure captures (pinned so expansion can re-read them)."""
        return set(node.args) | Expander.function_capture_ids(node.attrs)
