"""Distribution of a sequence: each distinct value with how often it occurs.

Written for parameter sweeps, where the interesting question after a per-case
arg-best is not the average parameter but which parameters keep winning. The
result is a sequence of [value, count] pairs ordered by value, so it prints as
a readable histogram and can be indexed like any other sequence.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._stats import materialize


def execute(**kwargs) -> list[list[Any]]:
    if "0" not in kwargs:
        raise ValueError("tally requires sequence argument at key '0'")
    items = materialize(kwargs["0"], name="tally")
    counts = Counter(items)
    return [[value, counts[value]] for value in sorted(counts)]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="tally",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.tally", kind="sequence"),
    kernel_name="default.tally",
    description="Distinct values of a sequence paired with their counts",
)
