"""Template formatting primitive."""

from __future__ import annotations

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs) -> str:
    """Apply Python-style `str.format` using positional arguments.

    Example:
    - `format_string("x_{:03d}", 7)` -> `"x_007"`
    """
    if "0" not in kwargs:
        raise ValueError("format_string requires template argument at key '0'")

    template = str(kwargs["0"])
    args: list[object] = []
    i = 1
    while str(i) in kwargs:
        args.append(kwargs[str(i)])
        i += 1

    try:
        return template.format(*args)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"format_string failed: {exc}") from exc


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="format_string",
    namespace="strings",
    kind="scalar",
    arity=AritySpec.variadic(min_args=1),
    attrs_schema={},
    planner=default_planner_factory("strings.format_string", kind="scalar"),
    kernel_name="strings.format_string",
    description="Format string with positional arguments",
)

