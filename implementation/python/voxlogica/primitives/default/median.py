"""Median of a numeric sequence.

Reported alongside the mean because they disagree in the informative case: a
mean far below its median says the average is being carried by a tail, not by
the typical case.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._stats import numbers


def execute(**kwargs) -> float:
    if "0" not in kwargs:
        raise ValueError("median requires sequence argument at key '0'")
    values = sorted(numbers(kwargs["0"], name="median"))
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return 0.5 * (values[middle - 1] + values[middle])


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="median",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.median", kind="scalar"),
    kernel_name="default.median",
    description="Median of a numeric sequence",
)
