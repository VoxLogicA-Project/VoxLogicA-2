"""Primitive that constructs an integer range as a Python list."""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _as_int(value, *, name: str) -> int:
    """Normalize range bounds to integers."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got bool")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be an integer, got float: {value!r}")
        return int(value)
    if isinstance(value, int):
        return value
    raise ValueError(f"{name} must be an integer, got: {type(value).__name__}")


def execute(**kwargs) -> list[int]:
    """Implement ``range(stop)`` and ``range(start, stop)`` semantics."""
    if "0" not in kwargs:
        raise ValueError("range requires at least one argument")
    if "1" in kwargs:
        start = _as_int(kwargs["0"], name="start")
        stop = _as_int(kwargs["1"], name="stop")
    else:
        start = 0
        stop = _as_int(kwargs["0"], name="stop")
    return list(range(start, stop))


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="range",
    namespace="default",
    kind="sequence",
    arity=AritySpec(min_args=1, max_args=2),
    attrs_schema={},
    planner=default_planner_factory("default.range", kind="sequence"),
    kernel_name="default.range",
    description="Create a sequence from integer range bounds",
)
