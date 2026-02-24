"""
Division primitive for VoxLogica-2

Implements division operation for numeric types.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(left, right):
    """
    Execute division operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Quotient of left and right
    """
    try:
        if right == 0:
            raise ValueError("Division by zero")
        result = left / right
        return result
    except Exception as e:
        raise ValueError(f"Division failed: {e}") from e


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="division",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.division", kind="scalar"),
    kernel_name="default.division",
    description="Division operation for numeric values",
)
