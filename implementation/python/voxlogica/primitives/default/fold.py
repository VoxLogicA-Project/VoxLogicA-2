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
    """Peel one element: `combine(fold(init, seq[:-1]), seq[-1])`.

    WHY A CHAIN AT ALL. The kernel below receives the whole materialized
    sequence and reduces it in Python, so every element is resident at once for
    a result that never needs more than two values in hand. As a chain each
    accumulator has exactly ONE consumer -- the next link -- and each element has
    one too, so both are released the moment they are used. Peak is one
    accumulator plus one element, whatever N is.

    WHY ONE LINK AT A TIME AND NOT THE WHOLE CHAIN. Building all N links in one
    rewrite registers N nodes at once, and the frontier then tracks the PLAN
    rather than the admission window -- measured as peak_frontier 65 on a
    64-element fold, against a bound of 40 that exists precisely to stop the
    frontier growing with the data. The memory was still fine, because the chain
    is sequential; the bookkeeping was not, and that is the shape of failure
    this engine has paid for before.

    So each rewrite peels the last element and leaves a shorter fold, which
    rewrites again when its turn comes. Three nodes per step, frontier bounded.

    Left-associated on purpose: a fold is left-to-right by definition, and a
    tree reduction would parallelize at the cost of holding N/2 partials.

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

    operator = str((node.attrs or {}).get("operator", ""))
    if operator not in _SUPPORTED_OPS:
        return None

    elements = ctx.resolve(sequence_id)
    if not isinstance(elements, (list, tuple)) or not elements:
        return None
    if not all(isinstance(item, Handle) for item in elements):
        return None            # nothing to point a link at; let the kernel do it

    if init_id is None:
        # THE DEFAULT SEED IS PART OF THE ANSWER. `fold -` with no init means
        # 0-e0-e1-..., and starting from e0 would silently give another number.
        # min and max are the ones that really do begin at the first element.
        seed = _DEFAULT_INIT.get(operator, USE_FIRST_ELEMENT)
        if seed is USE_FIRST_ELEMENT:
            if len(elements) == 1:
                return elements[0].node
            init_id = elements[0].node
            rest = _shorter_fold(ctx, node, sequence_id, init_id,
                                 start=1, stop=len(elements) - 1)
            return ctx.node("default.combine", rest, elements[-1].node,
                            operator=operator)
        init_id = ctx.constant(seed)

    if len(elements) == 1:
        return ctx.node("default.combine", init_id, elements[0].node,
                        operator=operator)
    rest = _shorter_fold(ctx, node, sequence_id, init_id,
                         start=0, stop=len(elements) - 1)
    return ctx.node("default.combine", rest, elements[-1].node, operator=operator)


def _shorter_fold(ctx: Any, node: Any, sequence_id: str, init_id: str,
                  *, start: int, stop: int) -> str:
    """A fold of the same operator over `sequence[start:stop]`."""
    shorter = ctx.node("default.subsequence", sequence_id,
                       ctx.constant(start), ctx.constant(stop))
    return ctx.node("default.fold", init_id, shorter,
                    operator=str((node.attrs or {}).get("operator", "")))


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
