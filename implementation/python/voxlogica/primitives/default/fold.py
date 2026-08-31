"""Fold primitive that reduces a sequence with a built-in combiner.

The reducer lowers ``fold op init seq`` and ``fold op seq`` into this
primitive. Only a fixed set of combiners is supported so fold stays
non-Turing-complete.
"""

from __future__ import annotations

from typing import Any, Callable

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default.addition import execute as add_execute
from voxlogica.primitives.default.division import execute as div_execute
from voxlogica.primitives.default.multiplication import execute as mul_execute
from voxlogica.primitives.default.subtraction import execute as sub_execute


class _UseFirstElement:
    """Sentinel init value for min/max folds without an explicit seed."""


USE_FIRST_ELEMENT = _UseFirstElement()

_DEFAULT_INIT: dict[str, Any] = {
    "+": 0,
    "-": 0,
    "*": 1,
    "&&": True,
    "||": False,
    "min": USE_FIRST_ELEMENT,
    "max": USE_FIRST_ELEMENT,
}

_SUPPORTED_OPS = frozenset(_DEFAULT_INIT)


def _combine(operator: str, left: Any, right: Any) -> Any:
    """Apply one supported fold combiner to two accumulated values."""
    if operator == "+":
        return add_execute(left, right)
    if operator == "-":
        return sub_execute(left, right)
    if operator == "*":
        return mul_execute(left, right)
    if operator == "/":
        return div_execute(left, right)
    if operator == "&&":
        return bool(left) and bool(right)
    if operator == "||":
        return bool(left) or bool(right)
    if operator == "min":
        return min(left, right)
    if operator == "max":
        return max(left, right)
    raise ValueError(f"Unsupported fold operator: {operator!r}")


def _materialize_iterable(value: Any) -> list[Any]:
    """Normalize supported sequence containers to a plain list."""
    if hasattr(value, "compute") and callable(value.compute):
        value = value.compute()
    if hasattr(value, "iter_values") and callable(value.iter_values):
        return list(value.iter_values())
    if isinstance(value, range):
        return list(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError("fold requires a sequence argument")


def fold_sequence(operator: str, init: Any, iterable: Any) -> Any:
    """Reduce ``iterable`` left-to-right using ``operator`` and optional ``init``."""
    op = str(operator)
    if op not in _SUPPORTED_OPS and op != "/":
        raise ValueError(
            f"Unsupported fold operator {op!r}; expected one of {sorted(_SUPPORTED_OPS | {'/'})}"
        )

    items = _materialize_iterable(iterable)
    if init is None:
        init = _DEFAULT_INIT[op]

    if init is USE_FIRST_ELEMENT:
        if not items:
            raise ValueError(f"fold {op} requires a non-empty sequence when no init is given")
        accumulator = items[0]
        start_index = 1
    else:
        accumulator = init
        start_index = 0

    combine: Callable[[Any, Any], Any] = lambda left, right: _combine(op, left, right)
    for item in items[start_index:]:
        accumulator = combine(accumulator, item)
    return accumulator


def execute(**kwargs) -> Any:
    """Reduce a sequence with a built-in combiner."""
    operator = kwargs.get("operator")
    if operator is None:
        raise ValueError("fold requires operator attribute")

    if "1" in kwargs:
        init = kwargs["0"]
        sequence = kwargs["1"]
    elif "0" in kwargs:
        init = None
        sequence = kwargs["0"]
    else:
        raise ValueError("fold requires a sequence argument")

    return fold_sequence(str(operator), init, sequence)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="fold",
    namespace="default",
    kind="scalar",
    arity=AritySpec(min_args=1, max_args=2),
    attrs_schema={"operator": str},
    planner=default_planner_factory("default.fold", kind="scalar"),
    kernel_name="default.fold",
    description="Reduce a sequence with a built-in combiner",
)
