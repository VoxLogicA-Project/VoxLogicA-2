"""Multiplication primitive for scalar values and aligned sequences."""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._sequence_math import apply_binary_op


def execute(left, right):
    """Return ``left * right`` using the shared scalar/sequence semantics."""
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
