"""Split a string on a separator."""

from __future__ import annotations

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs) -> list[str]:
    """Split argument 0 on separator 1 (default: comma). Returns the pieces.

    Exists so a program can read a CSV the dataset ships -- BraTS's
    name_mapping.csv, which is where the tumour grade lives -- and select cases
    by what the metadata says rather than by where they sort in a directory.
    """
    if "0" not in kwargs:
        raise ValueError("split requires a string argument at key '0'")
    return str(kwargs["0"]).split(str(kwargs.get("1", ",")))


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="split",
    namespace="strings",
    kind="sequence",
    arity=AritySpec(min_args=1, max_args=2),
    attrs_schema={},
    planner=default_planner_factory("strings.split", kind="sequence"),
    kernel_name="strings.split",
    description="Split a string on a separator (default comma)",
)
