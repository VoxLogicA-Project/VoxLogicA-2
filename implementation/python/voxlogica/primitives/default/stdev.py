"""Sample standard deviation of a numeric sequence."""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._stats import numbers


def execute(**kwargs) -> float:
    if "0" not in kwargs:
        raise ValueError("stdev requires sequence argument at key '0'")
    values = numbers(kwargs["0"], name="stdev")
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance ** 0.5


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="stdev",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.stdev", kind="scalar"),
    kernel_name="default.stdev",
    description="Sample standard deviation of a numeric sequence",
)
