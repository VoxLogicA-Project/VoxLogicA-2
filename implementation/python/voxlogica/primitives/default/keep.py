"""`keep(path, expr)` -- a value carried along in a file you name.

WHAT IT IS FOR. The result store is a cache: content-addressed, budgeted, and
evictable. Some values are not cache material -- a trained network is twenty-one
hours of GPU time -- and their owner wants them beside the program, in a file
they chose, surviving any decision the cache makes about space.

WHAT IT DOES, in order:

  1. the file exists and was written from THIS expression -> load it, and the
     expression is never scheduled;
  2. otherwise the expression is evaluated, WHICH THE NORMAL CACHE STILL
     ANSWERS -- if the store already holds it, nothing is recomputed -- and the
     value is written to the file on the way past.

So pointing `keep` at an expression whose value is already in the store
materialises that value into the file, which is how an already-trained model
becomes a file without retraining.

WHY IT IS A FUNCTION AND NOT SYNTAX. The language already has what this needs:
`rewrite`, the same mechanism `default.if` uses to leave a branch uncomputed and
`for_loop` uses to unroll. A keyword would buy nothing and cost a grammar; a
function composes, so `for g in cases do keep(path_of(g), f(g))` gives a file
per element for free.

WHAT IT IS NOT. `keep` preserves the VALUE. Where a value is a handle naming
artefacts elsewhere -- an nnU-Net model handle names a trainer directory -- the
file holds the handle, and the artefacts stay where the operator put them.
That is the right division (the operator already takes a `work_root` the author
chose) but it must be said, because a kept handle whose directory was deleted
is a file that loads and then fails downstream.

ONE COST, STATED. The value can end up in the store twice: once for the
expression, once under the node that writes the file. For the artefacts this
exists for -- models, handles, expensive singletons -- that is nothing. For a
large image kept in a tight loop it would not be.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default import _keepfile


def rewrite(node: Any, ctx: Any) -> str:
    """Become either a load from the file or a compute-and-write.

    The path is resolved -- it is cheap and strict, and the decision cannot be
    made without it. The expression is NOT resolved: not resolving it is the
    entire point, and `ctx.resolve` on it here would compute the thing the file
    exists to avoid.
    """
    path_id, inner = node.args[0], node.args[1]
    path = Path(str(ctx.resolve(path_id))).expanduser()
    found, _ = _keepfile.read(path, expect=inner)
    if found:
        # The id is checked again by the load kernel. Twice is deliberate: this
        # check decides the shape of the graph, that one decides what a worker
        # returns, and between the two a file can be replaced.
        return ctx.node("default.keep_load", path_id, expect=inner)
    # `expect` carries the id the file must be STAMPED with. Without it the
    # write would record an empty id and no later run could ever match it --
    # the file would be written once per run, forever, and never read.
    return ctx.node("default.keep_store", inner, path_id, expect=inner)


def execute(**kwargs):
    """Never called: this operator is rewritten, not computed."""
    raise RuntimeError(
        "default.keep is rewritten, not computed: reaching this kernel means "
        "the engine dispatched a rewrite node to the executor")


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="keep",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("default.keep", kind="scalar"),
    kernel_name="default.keep",
    rewrite=True,
    rewriter=rewrite,
    description="Carry a value along in a named file; load it if it is there",
)
