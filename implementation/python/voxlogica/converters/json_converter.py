"""JSON converter for symbolic WorkPlan objects."""

from __future__ import annotations

from typing import Optional, Dict, Any
import json
import dataclasses

from voxlogica.converters.common import coerce_plan, iter_sorted_nodes, node_arguments


class WorkPlanJSONEncoder(json.JSONEncoder):
    def default(self, o):  # noqa: D401
        return self._unwrap(o)

    def _unwrap(self, value):
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {
                key: self._unwrap(getattr(value, key))
                for key in value.__dataclass_fields__
            }
        if isinstance(value, dict):
            return {self._unwrap(k): self._unwrap(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._unwrap(v) for v in value]
        return value


def to_json(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> dict:
    """Convert WorkPlan to JSON-ready dict."""
    plan = coerce_plan(work_plan)
    encoder = WorkPlanJSONEncoder()

    nodes_list = []
    for node_id, node in iter_sorted_nodes(plan):
        if node.kind == "primitive":
            node_dict = {
                "id": node_id,
                "type": "operation",
                "operator": node.operator,
                "arguments": node_arguments(node),
                "attrs": encoder._unwrap(node.attrs),
                "output_kind": node.output_kind,
            }
        elif node.kind == "constant":
            node_dict = {
                "id": node_id,
                "type": "constant",
                "value": encoder._unwrap(node.attrs.get("value")),
                "output_kind": node.output_kind,
            }
        elif node.kind == "closure":
            node_dict = {
                "id": node_id,
                "type": "closure",
                "operator": node.operator,
                "arguments": node_arguments(node),
                "attrs": encoder._unwrap(node.attrs),
                "output_kind": node.output_kind,
            }
        else:
            continue

        if buffer_assignment and node_id in buffer_assignment:
            node_dict["buffer_id"] = buffer_assignment[node_id]

        nodes_list.append(node_dict)

    goals_list = [
        {
            "operation": goal.operation,
            "id": goal.id,
            "name": goal.name,
        }
        for goal in plan.goals
    ]

    return {
        "nodes": nodes_list,
        "goals": goals_list,
    }
