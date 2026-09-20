"""Read a kept value back. Made only by `keep`'s rewriter, never written by hand.

The expected id travels as an ATTRIBUTE rather than an argument, and that is
load-bearing twice over. As an argument it would be a dependency, so the engine
would compute the very expression the file replaces. As an attribute it still
enters this node's hash -- `hash_node` digests the attributes -- so replacing
the file with one holding a different expression's value changes this node's
identity and cannot be answered from the cache by the old value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default import _keepfile


def execute(**kwargs: Any) -> Any:
    path = Path(str(kwargs["0"])).expanduser()
    expect = str(kwargs.get("expect") or "")
    found, value = _keepfile.read(path, expect=expect)
    if not found:
        # Between the rewrite and this turn the file was removed or replaced.
        # Raising is right: silently returning something else would make the
        # program mean a different thing than it says.
        raise ValueError(
            f"kept file {path} no longer holds the value of {expect[:12]}: it "
            "was removed or rewritten while the run was in flight")
    return value


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="keep_load",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(1),
    attrs_schema={"expect": "string"},
    planner=default_planner_factory("default.keep_load", kind="scalar"),
    kernel_name="default.keep_load",
    description="Load a value from a kept file (made by keep, not by hand)",
)
