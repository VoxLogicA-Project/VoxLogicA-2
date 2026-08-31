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
    """Take ONE step: fold the next element in, and become a fold of the rest.

    WHY A GRAPH AT ALL. The kernel below receives the whole materialized sequence
    and reduces it in Python, so every element is resident at once for a result
    that never needs more than two values in hand. For 369 BraTS volumes that is
    the difference this engine is being changed for.

    WHY ONE STEP AND NOT THE WHOLE CHAIN. Building all N links at once registers
    N nodes at once, and that is what a `for` carefully does not do: admission
    opens a window of bodies, lets them complete, opens more. A chain looked like
    it could not be windowed, because link i cannot complete before link i-1.
    Wrong: they complete IN ORDER, so they can be made in order.

    AND THE FOLD ALIASES TO THE NEXT FOLD, it does not depend on it. An outer
    link waiting on an inner fold keeps both alive, and recursively all of them
    -- measured at 2N, worse than the whole chain. Forwarding leaves exactly one
    `combine` and one `fold` alive at a time, whatever N is.

    THE CURSOR IS AN ATTRIBUTE, so no slicing is needed: the remaining sequence
    is the SAME sequence node with a higher offset. N steps therefore add N
    `combine` nodes and N `fold` nodes and nothing else -- no tower of slices,
    and `resolve` keeps returning the one list of handles that is already
    resident. The offset is part of the node's identity, so interning stays
    honest: two folds at different offsets are different nodes.

    Result: peak memory of two values, frontier of O(1). A tail call, written as
    aliasing.

    Declines when the elements are not nodes -- a list of plain values has
    nothing to point a link at, and the kernel handles that as it always did.
    """
    args = node.args
    if len(args) == 2:
        init_id, sequence_id = args[0], args[1]
    elif len(args) == 1:
        init_id, sequence_id = None, args[0]
    else:
        return None

    attrs = node.attrs or {}
    operator = str(attrs.get("operator", ""))
    if operator not in _SUPPORTED_OPS:
        return None

    elements = ctx.resolve(sequence_id)
    if not isinstance(elements, (list, tuple)) or not all(
            isinstance(item, Handle) for item in elements):
        return None            # nothing to point a link at; let the kernel do it

    cursor = int(attrs.get("offset", 0) or 0)
    remaining = elements[cursor:]

    if init_id is None:
        # THE DEFAULT SEED IS PART OF THE ANSWER. `fold -` with no init means
        # 0-e0-e1-..., and starting from e0 would silently give another number.
        # min and max are the ones that really do begin at the first element.
        seed = _DEFAULT_INIT.get(operator, USE_FIRST_ELEMENT)
        if seed is USE_FIRST_ELEMENT:
            if not remaining:
                return None                # empty and no seed: the kernel raises
            init_id, cursor = remaining[0].node, cursor + 1
            remaining = remaining[1:]
        else:
            init_id = ctx.constant(seed)

    if not remaining:
        return init_id                     # nothing left to fold in

    step = ctx.node("default.combine", init_id, remaining[0].node, operator=operator)
    if len(remaining) == 1:
        return step
    return ctx.node("default.fold", step, sequence_id,
                    operator=operator, offset=cursor + 1)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="fold",
    namespace="default",
    kind="scalar",
    arity=AritySpec(min_args=1, max_args=2),
    attrs_schema={"operator": str, "offset": int},
    planner=default_planner_factory("default.fold", kind="scalar"),
    kernel_name="default.fold",
    rewrite=True,
    rewriter=rewrite,
    description="Reduce a sequence with a built-in combiner",
)
