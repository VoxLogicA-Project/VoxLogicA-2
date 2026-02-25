"""
Division primitive for VoxLogica-2

Implements division operation for numeric types.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._sequence_math import apply_binary_op


def execute(left, right):
    """
    Execute division operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Quotient of left and right
    """
    return apply_binary_op("Division", left, right, _safe_divide)


def _safe_divide(left, right):
    if right == 0:
        raise ValueError("Division by zero")
    return left / right


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
