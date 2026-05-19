"""Primitive that constructs a sequence from positional inputs.

The reducer emits this primitive for array literals so source order is preserved
explicitly in the runtime payload.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    """Reconstruct the ordered positional argument list at runtime."""
    ordered = sorted(kwargs.items(), key=lambda item: int(item[0]))
    return [value for _index, value in ordered]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="sequence",
    namespace="default",
    kind="sequence",
    arity=AritySpec.variadic(0),
    attrs_schema={},
    planner=default_planner_factory("default.sequence", kind="sequence"),
    kernel_name="default.sequence",
    description="Construct a sequence from literal elements",
)
