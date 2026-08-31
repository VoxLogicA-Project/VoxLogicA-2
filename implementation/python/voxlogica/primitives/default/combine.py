"""Apply one fold combiner to two values -- the link in a fold's chain.

`fold` used to be a loop inside a kernel: it received the whole materialized
sequence and reduced it in Python. That holds every element at once for a result
that only ever needs two values in hand.

Rewritten as a graph, a fold is a chain, and this is one of its links. Four of
the eight combiners already have primitives (`+ - * /`); `min`, `max`, `&&` and
`||` never did, because nothing but fold needed them. One node that takes the
combiner as an ATTRIBUTE covers all eight without adding four more modules, and
keeps the meaning of `fold`'s operator in the one place that already defines it.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default.fold import _SUPPORTED_OPS, _combine


def execute(**kwargs) -> Any:
    """Combine two values with the operator named in this node's attributes."""
    operator = str(kwargs.get("operator", ""))
    if operator not in _SUPPORTED_OPS:
        raise ValueError(f"combine: unsupported operator {operator!r}")
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("combine requires two values at keys '0' and '1'")
    return _combine(operator, kwargs["0"], kwargs["1"])


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="combine",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={"operator": str},
    planner=default_planner_factory("default.combine", kind="scalar"),
    kernel_name="default.combine",
    description="One link of a fold's chain",
)
