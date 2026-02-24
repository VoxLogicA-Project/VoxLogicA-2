"""Helpers to build symbolic plans."""

from __future__ import annotations

from typing import Any

from voxlogica.lazy.hash import hash_node
from voxlogica.lazy.ir import GoalSpec, NodeId, NodeSpec, SymbolicPlan


class SymbolicPlanner:
    """Mutable planner used by reducer, exporting immutable SymbolicPlan."""

    def __init__(self) -> None:
        self._nodes: dict[NodeId, NodeSpec] = {}
        self._goals: list[GoalSpec] = []
        self._imported_namespaces: list[str] = []

    def add_node(self, node: NodeSpec) -> NodeId:
        node_id = hash_node(node)
        if node_id not in self._nodes:
            self._nodes[node_id] = node
        return node_id

    def add_constant(self, value: Any, output_kind: str = "scalar") -> NodeId:
        return self.add_node(
            NodeSpec(
                kind="constant",
                operator="constant",
                attrs={"value": value},
                output_kind=output_kind,
            )
        )

    def add_goal(self, operation: str, node_id: NodeId, name: str) -> None:
        self._goals.append(GoalSpec(operation=operation, id=node_id, name=name))

    def import_namespace(self, namespace: str) -> None:
        if namespace not in self._imported_namespaces:
            self._imported_namespaces.append(namespace)

    def to_plan(self) -> SymbolicPlan:
        return SymbolicPlan(
            nodes=dict(self._nodes),
            goals=list(self._goals),
            imported_namespaces=tuple(self._imported_namespaces),
        )
