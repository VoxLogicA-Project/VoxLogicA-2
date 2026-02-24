"""Pure for-loop primitive used as a legacy fallback kernel."""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _apply_closure(closure: Any, value: Any) -> Any:
    if hasattr(closure, "apply") and callable(closure.apply):
        return closure.apply(value)
    if callable(closure):
        return closure(value)
    raise ValueError("for_loop closure is not callable")


def execute(**kwargs) -> list[Any]:
    """Apply closure across an iterable and return a list (strict fallback)."""
    if "0" not in kwargs:
        raise ValueError("for_loop requires iterable argument at key '0'")

    iterable = kwargs["0"]
    closure = kwargs.get("closure", kwargs.get("1"))
    if closure is None:
        raise ValueError("for_loop requires closure argument at key 'closure' or '1'")

    if hasattr(iterable, "compute") and callable(iterable.compute):
        iterable = iterable.compute()

    return [_apply_closure(closure, item) for item in iterable]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="for_loop",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.for_loop", kind="sequence"),
    kernel_name="default.for_loop",
    description="Apply a closure to each element of an iterable",
)
