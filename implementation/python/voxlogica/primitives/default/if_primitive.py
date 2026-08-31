"""A conditional that evaluates only the branch it takes.

There is no conditional in this language. `primitives/vox1/compat.imgql:68`
defines one arithmetically:

    let ifB(cond,th,el) = or(and(th,bconstant(cond)),and(el,not(bconstant(cond))))

It computes BOTH branches and masks them, because until now there was no way not
to: every argument of every node was materialized before the node ran. On images
that is twice the work and twice the memory, always.

This primitive is the smallest honest proof that `rewrite` is general rather
than a name for loop unrolling. It shares nothing with the loop machinery: it
declares `rewrite=True`, supplies a `rewriter`, and the engine forwards its value
from whichever branch it names. The other branch is never scheduled, so it is not
computed, not persisted, and not counted.

It is also an observable behaviour change, and that is the point rather than a
side effect: a program with a conditional computes fewer nodes, so less enters
the store and a warm re-run prunes differently.
"""

from __future__ import annotations

from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _is_true(value: Any) -> bool:
    """Truth for a condition, defined narrowly and on purpose.

    A conditional that silently treats an image, or an empty list, as false
    would be a source of wrong answers that never raise. Numbers and booleans
    decide; anything else says so.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    raise ValueError(
        f"a condition must be a number or a boolean, got {type(value).__name__}")


def rewrite(node: Any, resolve: Any) -> str:
    """Return the argument this node becomes: the taken branch.

    `resolve` is the engine's, called on the event loop -- the same standing the
    loop expander has when it materializes its iterable. The kernel never
    resolves anything, because there is no kernel.
    """
    condition, then_id, else_id = node.args[0], node.args[1], node.args[2]
    return then_id if _is_true(resolve(condition)) else else_id


def execute(**kwargs):
    """Never called: this operator is rewritten, not computed.

    Kept, and kept loud, because `for_loop` taught the lesson -- it has a kernel
    belonging to another strategy, and reaching it produced a confusing error
    about a closure. If this one is ever reached, the message says what happened.
    """
    raise RuntimeError(
        "default.if is rewritten, not computed: reaching this kernel means the "
        "engine dispatched a rewrite node to the executor")


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="if",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(3),
    attrs_schema={},
    planner=default_planner_factory("default.if", kind="scalar"),
    kernel_name="default.if",
    rewrite=True,
    rewriter=rewrite,
    description="Take one branch; the other is never computed",
)
