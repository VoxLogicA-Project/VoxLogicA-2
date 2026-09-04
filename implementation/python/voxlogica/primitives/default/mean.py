"""Arithmetic mean of a numeric sequence."""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._stats import numbers


def execute(**kwargs) -> float:
    if "0" not in kwargs:
        raise ValueError("mean requires sequence argument at key '0'")
    values = numbers(kwargs["0"], name="mean")
    return sum(values) / len(values)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="mean",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.mean", kind="scalar"),
    kernel_name="default.mean",
    description="Arithmetic mean of a numeric sequence",
)
