"""
DOT (Graphviz) converter for WorkPlan objects
"""

from typing import Optional, Dict, Any
from voxlogica.reducer import Operation, ConstantValue


def to_dot(work_plan: Any, buffer_assignment: Optional[Dict[str, int]] = None) -> str:
    """Convert WorkPlan to DOT (Graphviz) format
    
    Args:
        work_plan: The WorkPlan to convert
        buffer_assignment: Optional mapping of operation IDs to buffer IDs
        
    Returns:
        DOT format string representation of the WorkPlan
    """
    dot_str = "digraph {\n"
    for node_id, node in work_plan.nodes.items():
        if isinstance(node, Operation):
            op_name = str(node.operator)
            op_label = f"{op_name}"
            if buffer_assignment and node_id in buffer_assignment:
                buffer_id = buffer_assignment[node_id]
                op_label = f"{op_name}\\nbuf:{buffer_id}"
            dot_str += f'  "{node_id}" [label="{op_label}"]\n'
            for argument in node.arguments.values():
                dot_str += f'  "{argument}" -> "{node_id}";\n'
        elif isinstance(node, ConstantValue):
            value_label = f"const: {repr(node.value)}"
            if buffer_assignment and node_id in buffer_assignment:
                buffer_id = buffer_assignment[node_id]
                value_label += f"\\nbuf:{buffer_id}"
            dot_str += f'  "{node_id}" [label="{value_label}"]\n'
    dot_str += "}\n"
    return dot_str
