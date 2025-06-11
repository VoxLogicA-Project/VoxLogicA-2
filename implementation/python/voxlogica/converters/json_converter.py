"""
JSON converter for WorkPlan objects
"""

from typing import Optional, Dict, Any
import json
import dataclasses
from voxlogica.reducer import Operation, ConstantValue


class WorkPlanJSONEncoder(json.JSONEncoder):
    def default(self, o):
        # Always return a fully unwrapped, JSON-serializable object
        return self._unwrap(o)

    def _unwrap(self, v):
        import dataclasses
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            return {k: self._unwrap(getattr(v, k)) for k in v.__dataclass_fields__}
        if isinstance(v, dict):
            return {self._unwrap(k): self._unwrap(val) for k, val in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [self._unwrap(i) for i in v]
        # Handle ConstantValue objects specially
        if isinstance(v, ConstantValue):
            return v.value
        return v


def to_json(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> dict:
    """Convert WorkPlan to JSON format
    
    Args:
        work_plan: The WorkPlan to convert
        buffer_assignment: Optional mapping of operation IDs to buffer IDs
        
    Returns:
        Dictionary representation suitable for JSON serialization
    """
    def unwrap(v):
        import dataclasses
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            return {k: WorkPlanJSONEncoder()._unwrap(getattr(v, k)) for k in v.__dataclass_fields__}
        if isinstance(v, dict):
            return {WorkPlanJSONEncoder()._unwrap(k): WorkPlanJSONEncoder()._unwrap(val) for k, val in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [WorkPlanJSONEncoder()._unwrap(i) for i in v]
        if isinstance(v, ConstantValue):
            return v.value
        return v

    nodes_list = []
    for node_id, node in work_plan.nodes.items():
        if isinstance(node, Operation):
            node_dict = {
                "id": node_id,
                "type": "operation",
                "operator": WorkPlanJSONEncoder()._unwrap(node.operator),
                "arguments": WorkPlanJSONEncoder()._unwrap(node.arguments),
            }
        elif isinstance(node, ConstantValue):
            node_dict = {
                "id": node_id,
                "type": "constant",
                "value": WorkPlanJSONEncoder()._unwrap(node.value),
            }
        else:
            continue
        if buffer_assignment and node_id in buffer_assignment:
            node_dict["buffer_id"] = buffer_assignment[node_id]
        nodes_list.append(node_dict)

    goals_list = []
    for goal in work_plan.goals:
        goals_list.append({
            "operation": WorkPlanJSONEncoder()._unwrap(goal.operation),
            "id": goal.id,
            "name": goal.name,
        })

    return {
        "nodes": nodes_list,
        "goals": goals_list,
    }
