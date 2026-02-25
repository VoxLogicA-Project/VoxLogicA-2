"""DOT (Graphviz) converter for symbolic WorkPlan objects."""

from __future__ import annotations

from typing import Optional, Dict, Any

from voxlogica.converters.common import coerce_plan, iter_sorted_nodes


def _node_arguments(node: Any) -> list[str]:
    args = list(getattr(node, "args", ()))
    kwargs = [value for _, value in getattr(node, "kwargs", ())]
    return args + kwargs


def to_dot(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> str:
    """Convert WorkPlan to DOT format."""
    plan = coerce_plan(work_plan)

    lines = ["digraph {"]

    for node_id, node in iter_sorted_nodes(plan):
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
