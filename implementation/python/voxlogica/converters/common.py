"""Shared converter helpers for symbolic plan rendering."""

from __future__ import annotations

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


def node_arguments(node: Any) -> dict[str, Any]:
    """Return node args/kwargs in canonical dictionary form."""
    arguments = {str(index): arg for index, arg in enumerate(getattr(node, "args", ()))}  # noqa: C416
    arguments.update(dict(getattr(node, "kwargs", ())))
    return arguments
