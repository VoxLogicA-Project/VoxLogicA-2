"""Index access primitive for sequence-like values.

This primitive supports both eager Python containers and the runtime's
``SequenceValue`` abstraction so indexing works uniformly across literal and
computed sequences.
"""

from __future__ import annotations

from collections.abc import Iterable

from voxlogica.execution_strategy.results import SequenceValue

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _as_int(value, *, name: str) -> int:
    """Normalize index-like values to integers with clear error messages."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{name} must be an integer, got float: {value!r}")
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{name} must be an integer, got string: {value!r}") from exc
    raise ValueError(f"{name} must be an integer, got: {type(value).__name__}")


def execute(**kwargs):
    """Return the element at the given index from a sequence-like input."""
    tuple_value = kwargs["0"]
    idx = _as_int(kwargs["1"], name="idx")

    if idx < 0:
        raise ValueError(f"Index argument must be non-negative, got: {idx}")

    if isinstance(tuple_value, SequenceValue):
        for current_index, item in enumerate(tuple_value.iter_values()):
            print("iterating")
            if current_index == idx:
                return item
        raise IndexError(f"Sequence index out of range: {idx}")

    # Generic iterables may not support direct indexing, so scan until the
    # requested position is reached.
    if isinstance(tuple_value, Iterable) and not isinstance(tuple_value, (list, tuple, range, str, bytes, bytearray)):
        for current_index, item in enumerate(tuple_value):
            print("Iterating")
            if current_index == idx:
                return item
        raise IndexError(f"Sequence index out of range: {idx}")

    return tuple_value[idx]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="index",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.index", kind="scalar"),
    kernel_name="default.index",
    description="Tuple/list index access",
)
