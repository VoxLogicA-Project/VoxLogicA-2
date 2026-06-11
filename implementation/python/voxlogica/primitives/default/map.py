"""Map primitive that applies a closure across a sequence.

The reducer uses this primitive for higher-order sequence mapping. The strict
runtime reconstructs the closure object and this kernel materializes the mapped
result as a Python list.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _apply(func: Any, value: Any) -> Any:
    """Call the runtime closure protocol or a plain Python callable."""
    if hasattr(func, "apply") and callable(func.apply):
        return func.apply(value)
    if callable(func):
        return func(value)
    raise ValueError("map expects a callable closure")


def execute(**kwargs) -> list[Any]:
    """Materialize the mapped sequence eagerly under the strict runtime."""
    if "0" not in kwargs:
        raise ValueError("map requires sequence argument at key '0'")

    if len(kwargs)>2:
        start = kwargs["0"]
        stop = kwargs["1"]
        sequence = kwargs["2"][int(start):int(stop)]
        closure = kwargs.get("3", kwargs.get("closure"))
    else:
        sequence = kwargs["0"]
        closure = kwargs.get("1", kwargs.get("closure"))
    if closure is None:
        raise ValueError("map requires closure argument at key '1' or 'closure'")

    if hasattr(sequence, "compute") and callable(sequence.compute):
        sequence = sequence.compute()

    return [_apply(closure, item) for item in sequence]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="map",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.map", kind="sequence"),
    kernel_name="default.map",
    description="Map a closure over a sequence",
)
