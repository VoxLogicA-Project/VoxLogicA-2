"""Primitive that constructs a sequence from positional inputs.

The reducer emits this primitive for array literals so source order is preserved
explicitly in the runtime payload, and the loop expander interns one of these to
gather the bodies it reduced.

LAZY, and this is the operator the whole handle design exists for. The kernel
below never looks inside what it is given -- it puts its arguments in a list in
order -- and yet it was once handed 51.4 GB of volumes to do it (issue #51),
because the executor resolved every argument to a value first. Given handles it
does exactly the same work on 309 hashes.

The node's ARGUMENTS are unchanged: it still depends on every element, so the
graph's refcount still keeps them alive and releases them on completion. What
changes is that the elements are now individually resident values the governor
can spill and evict, instead of one gathered list that could be neither.
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    """Reconstruct the ordered positional argument list at runtime.

    Receives handles, returns handles: nothing is materialized here.
    """
    ordered = sorted(kwargs.items(), key=lambda item: int(item[0]))
    return [value for _index, value in ordered]


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="sequence",
    namespace="default",
    kind="sequence",
    arity=AritySpec.variadic(0),
    attrs_schema={},
    planner=default_planner_factory("default.sequence", kind="sequence"),
    kernel_name="default.sequence",
    lazy=True,
    description="Construct a sequence from literal elements",
)
