"""
Addition primitive for VoxLogica-2

Implements addition operation for numeric types.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(left, right):
    """
    Execute addition operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Sum of left and right
    """
    try:
        result = left + right
        return result
    except Exception as e:
        raise ValueError(f"Addition failed: {e}") from e


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="addition",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.addition", kind="scalar"),
    kernel_name="default.addition",
    description="Addition operation for numeric values",
)
