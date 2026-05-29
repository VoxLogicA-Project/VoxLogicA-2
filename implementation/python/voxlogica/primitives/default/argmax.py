"""Argmax primitive that returns the index of the largest sequence element."""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _materialize_iterable(value: Any) -> list[Any]:
    """Normalize supported sequence containers to a plain list."""
    if hasattr(value, "compute") and callable(value.compute):
        value = value.compute()
    if hasattr(value, "iter_values") and callable(value.iter_values):
        return list(value.iter_values())
    if isinstance(value, range):
        return list(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError("argmax requires a sequence argument")


def argmax_sequence(iterable: Any) -> int:
    """Return the index of the maximum value; ties pick the first occurrence."""
    items = _materialize_iterable(iterable)
    if not items:
        raise ValueError("argmax requires a non-empty sequence")

    best_index = 0
    best_value = items[0]
    for index, item in enumerate(items[1:], start=1):
        if item > best_value:
            best_index = index
            best_value = item
    return best_index


def execute(**kwargs) -> int:
    """Return the index of the largest element in a sequence."""
    if "0" not in kwargs:
        raise ValueError("argmax requires sequence argument at key '0'")
    return argmax_sequence(kwargs["0"])


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="argmax",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.argmax", kind="scalar"),
    kernel_name="default.argmax",
    description="Index of the maximum element in a sequence",
)
