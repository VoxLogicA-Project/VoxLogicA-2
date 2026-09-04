"""Reduce a bytes-valued node to a scalar, for benchmark variants where a
large intermediate must NOT survive to the loop's final sequence assembly."""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    buf = kwargs["0"]
    return float(sum(buf[:4096]))  # cheap partial reduction; O(1) in payload size


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="shrink",
    namespace="test",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={},
    planner=default_planner_factory("test.shrink", kind="scalar"),
    kernel_name="test.shrink",
    description="Reduce an array value to a scalar mean: shrink(arr)",
)
