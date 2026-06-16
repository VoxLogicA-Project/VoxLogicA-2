"""Filter primitive that keeps sequence items matching a predicate closure.

The reducer lowers ``filter item in seq do pred`` into this primitive plus a
symbolic closure node. Items are retained when the predicate evaluates truthy.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _apply_closure(closure: Any, value: Any) -> Any:
    """Call either a runtime closure object or a plain Python callable."""
    if hasattr(closure, "apply") and callable(closure.apply):
        return closure.apply(value)
    if callable(closure):
        return closure(value)
    raise ValueError("filter closure is not callable")


def _is_truthy(value: Any) -> bool:
    """Return whether a predicate result should keep the current item."""
    return bool(value)


def execute(**kwargs) -> list[Any]:
    """Keep items from an iterable when the predicate closure is truthy."""
    if "0" not in kwargs:
        raise ValueError("filter requires iterable argument at key '0'")

    iterable = kwargs["0"]
    closure = kwargs.get("closure", kwargs.get("1"))
    if closure is None:
        raise ValueError("filter requires closure argument at key 'closure' or '1'")

    if hasattr(iterable, "compute") and callable(iterable.compute):
        if "2" in kwargs:
            start = kwargs["2"]
            stop = kwargs["3"]
            iterable = iterable.compute()[start:stop]
        else:
            iterable = iterable.compute()

    kept: list[Any] = []
    for item in iterable:
        if _is_truthy(_apply_closure(closure, item)):
            kept.append(item)
    return kept


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="filter",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.filter", kind="sequence"),
    kernel_name="default.filter",
    description="Keep sequence items that satisfy a predicate closure",
)
