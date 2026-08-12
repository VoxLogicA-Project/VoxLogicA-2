"""Indices that would sort a sequence ascending.

The declarative way to ask "which cases were worst": argsort the scores, then
index the case list with the first few. Ties keep their original order, so the
result is deterministic for a content-addressed run.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default._stats import numbers


def execute(**kwargs) -> list[int]:
    if "0" not in kwargs:
        raise ValueError("argsort requires sequence argument at key '0'")
    values = numbers(kwargs["0"], name="argsort")
    return [index for index, _ in sorted(enumerate(values), key=lambda pair: pair[1])]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="argsort",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("default.argsort", kind="sequence"),
    kernel_name="default.argsort",
    description="Indices that sort a numeric sequence ascending (stable)",
)
