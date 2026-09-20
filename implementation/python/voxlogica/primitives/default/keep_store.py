"""Forward a value and leave a copy in a named file. Made by `keep`'s rewriter.

This is the node that makes "materialise what the cache already has" work: its
argument is the ordinary expression, so the ordinary store answers for it if it
can, and this kernel then sees a value it did not have to compute and writes it
out.

A failed write does not fail the run. The file is a convenience -- the value is
correct either way -- and taking down a computation because a directory is
read-only would be the wrong trade. It is logged, loudly, once per path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.default import _keepfile

logger = logging.getLogger(__name__)

#: Paths already complained about, so a loop does not fill the log.
_WARNED: set[str] = set()


def execute(**kwargs: Any) -> Any:
    value = kwargs["0"]
    path = Path(str(kwargs["1"])).expanduser()
    expect = str(kwargs.get("expect") or "")
    try:
        if not path.exists():
            _keepfile.write(path, expect, value)
    except Exception as exc:                                    # noqa: BLE001
        key = str(path)
        if key not in _WARNED:
            _WARNED.add(key)
            logger.error("keep could not write %s (%s); the value is still "
                         "correct and the run continues", path, exc)
    return value


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="keep_store",
    namespace="default",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={"expect": "string"},
    planner=default_planner_factory("default.keep_store", kind="scalar"),
    kernel_name="default.keep_store",
    description="Forward a value, writing it to a kept file (made by keep)",
)
