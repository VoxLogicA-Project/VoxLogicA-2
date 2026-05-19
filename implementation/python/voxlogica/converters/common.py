"""Shared helpers for plan rendering.

Converters accept either mutable reducer output or immutable symbolic plans.
These functions normalize that difference so renderers can focus on formatting.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def coerce_plan(work_plan: Any):
    """Return a symbolic plan view from WorkPlan or SymbolicPlan-like object."""
    if hasattr(work_plan, "to_symbolic_plan"):
        return work_plan.to_symbolic_plan()
    return work_plan


def iter_sorted_nodes(work_plan: Any):
    """Iterate nodes in deterministic ID order."""
    plan = coerce_plan(work_plan)
    nodes = getattr(plan, "nodes", {})
    for node_id in sorted(nodes.keys()):
        yield node_id, nodes[node_id]


def node_dependency_ids(node: Any) -> tuple[str, ...]:
    """Return node dependencies in execution argument order."""
    args = tuple(getattr(node, "args", ()))
    kwargs = tuple(value for _, value in getattr(node, "kwargs", ()))
    function_captures = _function_capture_dependency_ids(
        dict(getattr(node, "attrs", {})).get("function_captures", {})
    )
    return args + kwargs + function_captures


def _function_capture_dependency_ids(function_captures: Any) -> tuple[str, ...]:
    dependencies: list[str] = []
    for spec in dict(function_captures or {}).values():
        captures = dict(spec.get("captures", {}))
        dependencies.extend(str(node_id) for node_id in captures.values())
        dependencies.extend(_function_capture_dependency_ids(spec.get("functions", {})))
    return tuple(dependencies)


def iter_topological_nodes(work_plan: Any) -> Iterator[tuple[str, Any]]:
    """Iterate nodes in dependency-first topological order.

    Ties are broken by node id so the output stays deterministic. This order is
    suitable for sequential executors that materialize each node once all of its
    dependencies have already been materialized.
    """
    plan = coerce_plan(work_plan)
    nodes = getattr(plan, "nodes", {})
    ordered: list[tuple[str, Any]] = []
    permanently_marked: set[str] = set()
    temporarily_marked: set[str] = set()

    def visit(node_id: str, path: tuple[str, ...]) -> None:
        if node_id in permanently_marked:
            return
        if node_id in temporarily_marked:
            cycle = " -> ".join(path + (node_id,))
            raise ValueError(f"Cycle detected in symbolic DAG: {cycle}")
        if node_id not in nodes:
            raise KeyError(f"Node dependency is missing from symbolic DAG: {node_id}")

        temporarily_marked.add(node_id)
        node = nodes[node_id]
        next_path = path + (node_id,)
        for dependency_id in sorted(node_dependency_ids(node)):
            visit(dependency_id, next_path)
        temporarily_marked.remove(node_id)

        permanently_marked.add(node_id)
        ordered.append((node_id, node))

    for node_id in sorted(nodes.keys()):
        visit(node_id, ())

    yield from ordered


def node_arguments(node: Any) -> dict[str, Any]:
    """Return node args/kwargs in canonical dictionary form."""
    arguments = {str(index): arg for index, arg in enumerate(getattr(node, "args", ()))}  # noqa: C416
    arguments.update(dict(getattr(node, "kwargs", ())))
    return arguments
