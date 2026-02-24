"""
Subtraction primitive for VoxLogica-2

Implements subtraction operation for numeric types.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(left, right):
    """
    Execute subtraction operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Difference of left and right
    """
    try:
        result = left - right
        return result
    except Exception as e:
        raise ValueError(f"Subtraction failed: {e}") from e


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="subtraction",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.subtraction", kind="scalar"),
    kernel_name="default.subtraction",
    description="Subtraction operation for numeric values",
)
