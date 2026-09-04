"""Primitive that builds a Cartesian grid from independent parameter axes."""

from __future__ import annotations

from itertools import product
from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _materialize(value: Any, *, name: str) -> list[Any]:
    """Normalize eager, computed, and lazy sequence values."""
    if hasattr(value, "compute") and callable(value.compute):
        value = value.compute()
    if hasattr(value, "iter_values") and callable(value.iter_values):
        return list(value.iter_values())
    if isinstance(value, (list, tuple, range)):
        return list(value)
    raise ValueError(f"{name} must be a sequence")


def execute(**kwargs) -> list[list[Any]]:
    """Return one parameter vector for every combination of the supplied axes."""
    if "0" not in kwargs:
        raise ValueError("parameter_grid requires a sequence of axes")
    axes = _materialize(kwargs["0"], name="axes")
    materialized_axes = [_materialize(axis, name=f"axis {index}") for index, axis in enumerate(axes)]
    return [list(values) for values in product(*materialized_axes)]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="parameter_grid",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.parameter_grid", kind="sequence"),
    kernel_name="default.parameter_grid",
    description="Build the Cartesian product of independent parameter axes",
)
