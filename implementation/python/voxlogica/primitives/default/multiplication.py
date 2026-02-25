"""
Multiplication primitive for VoxLogica-2

Implements multiplication operation for numeric types.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._sequence_math import apply_binary_op


def execute(left, right):
    """
    Execute multiplication operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Product of left and right
    """
    return apply_binary_op(
        "Multiplication",
        left,
        right,
        lambda left_value, right_value: left_value * right_value,
    )


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="multiplication",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.multiplication", kind="scalar"),
    kernel_name="default.multiplication",
    description="Multiplication operation for numeric values",
)
