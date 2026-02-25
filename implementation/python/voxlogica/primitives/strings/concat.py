"""String concatenation primitive."""

from __future__ import annotations

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs) -> str:
    """Concatenate positional arguments as strings."""
    parts: list[str] = []
    i = 0
    while str(i) in kwargs:
        parts.append(str(kwargs[str(i)]))
        i += 1

    if not parts:
        raise ValueError("concat requires at least one argument")

    return "".join(parts)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="concat",
    namespace="strings",
    kind="scalar",
    arity=AritySpec.variadic(min_args=1),
    attrs_schema={},
    planner=default_planner_factory("strings.concat", kind="scalar"),
    kernel_name="strings.concat",
    description="Concatenate values as strings",
)

