"""Render symbolic plans as Graphviz DOT.

The DOT output is intended for human inspection of dependency structure rather
than for execution.
"""

from __future__ import annotations

from typing import Optional, Dict, Any

from voxlogica.converters.common import coerce_plan, iter_topological_nodes, node_dependency_ids


def _node_arguments(node: Any) -> list[str]:
    """Return every dependency node id, flattening args and kwargs alike."""
    return list(node_dependency_ids(node))


def to_dot(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> str:
    """Convert a symbolic plan into Graphviz DOT text."""
    plan = coerce_plan(work_plan)

    lines = ["digraph {"]

    for node_id, node in iter_topological_nodes(plan):
        if node.kind == "primitive":
            label = node.operator
            if buffer_assignment and node_id in buffer_assignment:
                label = f"{label}\\nbuf:{buffer_assignment[node_id]}"
            lines.append(f'  "{node_id}" [label="{label}"]')
            for dep_id in _node_arguments(node):
                lines.append(f'  "{dep_id}" -> "{node_id}";')

        elif node.kind == "constant":
            value_label = f"const: {repr(node.attrs.get('value'))}"
            if buffer_assignment and node_id in buffer_assignment:
                value_label = f"{value_label}\\nbuf:{buffer_assignment[node_id]}"
            lines.append(f'  "{node_id}" [label="{value_label}"]')

        elif node.kind == "closure":
            variable = node.attrs.get("parameter", "arg")
            label = f"closure({variable})"
            lines.append(f'  "{node_id}" [label="{label}"]')
            for dep_id in _node_arguments(node):
                lines.append(f'  "{dep_id}" -> "{node_id}";')

    lines.append("}")
    return "\n".join(lines) + "\n"
