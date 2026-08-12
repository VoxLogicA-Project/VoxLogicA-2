"""Largest integer not greater than a number.

Needed wherever a computed position has to become an index: a midpoint between
two planes is generally not a plane.
"""

from __future__ import annotations

import math
from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs) -> int:
    if "0" not in kwargs:
        raise ValueError("floor requires a numeric argument at key '0'")
    value = kwargs["0"]
    try:
        return math.floor(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("floor requires a number") from exc


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="floor",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.floor", kind="scalar"),
    kernel_name="default.floor",
    description="Largest integer not greater than a number",
)
