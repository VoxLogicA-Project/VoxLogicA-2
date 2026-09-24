"""String equality."""

from __future__ import annotations

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs) -> bool:
    """True when arguments 0 and 1 are the same string.

    The language has no equality operator on strings, so until now a program
    could read a value out of a file but not ask what it was. This is the
    smallest thing that lets `filter` keep the rows of a CSV whose first field
    says "HGG".
    """
    if "0" not in kwargs or "1" not in kwargs:
        raise ValueError("equals requires two arguments at keys '0' and '1'")
    return str(kwargs["0"]) == str(kwargs["1"])


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="equals",
    namespace="strings",
    kind="scalar",
    arity=AritySpec(min_args=2, max_args=2),
    attrs_schema={},
    planner=default_planner_factory("strings.equals", kind="scalar"),
    kernel_name="strings.equals",
    description="True when two values are the same string",
)
