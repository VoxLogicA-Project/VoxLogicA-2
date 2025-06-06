"""
JSON converter for WorkPlan objects
"""

from typing import Optional, Dict, Any


def to_json(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> dict:
    """Convert WorkPlan to JSON format
    
    Args:
        work_plan: The WorkPlan to convert
        buffer_assignment: Optional mapping of operation IDs to buffer IDs
        
    Returns:
        Dictionary representation suitable for JSON serialization
    """
    operations_list = []
    for op_id, op in work_plan.operations.items():
        op_dict = {
            "id": op_id,
            "operator": op.operator,
            "arguments": op.arguments,
        }
        if buffer_assignment and op_id in buffer_assignment:
            op_dict["buffer_id"] = buffer_assignment[op_id]
        operations_list.append(op_dict)
    
    goals_list = []
    for operation_id in work_plan.goals:
        goals_list.append({
            "operation_id": operation_id,
        })
    
    return {
        "operations": operations_list,
        "goals": goals_list,
    }
