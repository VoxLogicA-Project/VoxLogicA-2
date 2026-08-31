"""Keep the elements a sequence of flags marks -- without materializing any.

The second half of `filter`. The first half is an ordinary `map` of the
predicate over the sequence, which the engine already knows how to expand; this
node then takes the flags it produced and the ORIGINAL sequence, and returns the
elements whose flag is true.

The point is what it does not do. Its sequence argument arrives SHALLOW, so it
holds handles, and the result is the kept handles. No element is ever a value
here: filtering three hundred volumes down to four costs three hundred booleans
and four references, not three hundred volumes.

That is also why `shallow` had to become per-argument. The flags must be values
-- there is nothing to test in a handle -- and the elements must not be. One
word for the whole operator could not say both.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def execute(**kwargs) -> list[Any]:
    """Return the elements of argument 1 whose flag in argument 0 is true."""
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("gather requires flags at key '0' and a sequence at key '1'")
    flags = list(kwargs["0"])
    elements = list(kwargs["1"])
    if len(flags) != len(elements):
        raise ValueError(
            f"gather: {len(flags)} flags for {len(elements)} elements")
    return [element for element, flag in zip(elements, flags) if _is_truthy(flag)]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="gather",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.gather", kind="sequence"),
    kernel_name="default.gather",
    shallow=(1,),
    description="Keep the elements their flags mark",
)
