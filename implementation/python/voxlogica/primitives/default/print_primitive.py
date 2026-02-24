"""Print primitive for VoxLogicA."""

from __future__ import annotations

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    """Print label=value and return the rendered string."""
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("print_primitive requires keys '0' (label) and '1' (value)")

    label = kwargs["0"]
    value = kwargs["1"]

    if isinstance(label, str) and label.startswith('"') and label.endswith('"'):
        label = label[1:-1]

    rendered = f"{label}={value}"
    print(rendered)
    return rendered


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="print_primitive",
    namespace="default",
    kind="effect",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.print_primitive", kind="effect"),
    kernel_name="default.print_primitive",
    description="Render and print a label/value pair",
)
