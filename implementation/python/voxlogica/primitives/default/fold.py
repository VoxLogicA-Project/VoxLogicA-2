"""Fold primitive that reduces a sequence with a built-in combiner.

The reducer lowers ``fold op init seq`` and ``fold op seq`` into this
primitive. Only a fixed set of combiners is supported so fold stays
non-Turing-complete.
"""

from __future__ import annotations

from typing import Any, Callable

from voxlogica.handles import Handle
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


def rewrite(node: Any, ctx: Any):
    """Turn a fold into the chain it is: combine(combine(combine(init,e0),e1),e2).

    WHY A CHAIN AND NOT A LOOP IN THE KERNEL. The kernel below receives the whole
    materialized sequence and reduces it in Python, so every element is resident
    at once for a result that never needs more than two values in hand. As a
    chain each accumulator has exactly ONE consumer -- the next link -- and each
    element has one too, so both are released the moment they are used. Peak is
    one accumulator plus one element, whatever N is.

    Left-associated on purpose. A tree reduction would parallelize but hold N/2
    partial results, and a fold is left-to-right by definition, so the chain is
    both the correct shape and the cheap one.

    Declines when the sequence's elements are not nodes -- a list of plain values
    has no node to point a link at, and inventing constants for them would trade
    the memory back for graph. The kernel handles that case, as it always did.
    """
    args = node.args
    if len(args) == 2:
        init_id, sequence_id = args[0], args[1]
    elif len(args) == 1:
        init_id, sequence_id = None, args[0]
    else:
        return None

    operator = str((node.attrs or {}).get("operator", ""))
    if operator not in _SUPPORTED_OPS:
        return None

    elements = ctx.resolve(sequence_id)
    if not isinstance(elements, (list, tuple)) or not elements:
        return None
    if not all(isinstance(item, Handle) for item in elements):
        return None            # nothing to point a link at; let the kernel do it

    accumulator = init_id
    for handle in elements:
        accumulator = (handle.node if accumulator is None
                       else ctx.node("default.combine", accumulator, handle.node,
                                     operator=operator))
    return accumulator


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="fold",
    namespace="default",
    kind="scalar",
    arity=AritySpec(min_args=1, max_args=2),
    attrs_schema={"operator": str},
    planner=default_planner_factory("default.fold", kind="scalar"),
    kernel_name="default.fold",
    rewrite=True,
    rewriter=rewrite,
    description="Reduce a sequence with a built-in combiner",
)
