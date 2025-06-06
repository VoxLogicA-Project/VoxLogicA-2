"""
DOT (Graphviz) converter for WorkPlan objects
"""

from typing import Optional, Dict, Any


def to_dot(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> str:
    """Convert WorkPlan to DOT (Graphviz) format
    
    Args:
        work_plan: The WorkPlan to convert
        buffer_assignment: Optional mapping of operation IDs to buffer IDs
        
    Returns:
        DOT format string representation of the WorkPlan
    """
    dot_str = "digraph {\n"
    for op_id, op in work_plan.operations.items():
        op_name = str(op.operator)
        op_label = f"{op_name}"
        
        if buffer_assignment and op_id in buffer_assignment:
            buffer_id = buffer_assignment[op_id]
            op_label = f"{op_name}\\nbuf:{buffer_id}"
        
        dot_str += f'  "{op_id}" [label="{op_label}"]\n'
        
        for argument in op.arguments.values():
            dot_str += f'  "{argument}" -> "{op_id}";\n'
    
    dot_str += "}\n"
    return dot_str
