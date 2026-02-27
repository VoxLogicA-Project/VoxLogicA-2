"""Directory listing primitive with optional glob filtering."""

from __future__ import annotations

from pathlib import Path

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _to_bool(value: object, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"dir {name} must be boolean-like, got: {value!r}")


def execute(**kwargs) -> list[str]:
    """List file/directory names under a root directory.

    Positional arguments:
    - `0` (required): root directory path
    - `1` (optional): glob pattern (default `*`)
    - `2` (optional): recursive flag (default `false`)
    - `3` (optional): full_paths flag (default `false`)
    """
    if "0" not in kwargs:
        raise ValueError("dir requires root directory argument at key '0'")

    root = Path(str(kwargs["0"])).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"dir root not found: {root}")
    if not root.is_dir():
        raise ValueError(f"dir root is not a directory: {root}")

    pattern = str(kwargs.get("1", "*"))
    recursive = _to_bool(kwargs.get("2", False), name="recursive")
    full_paths = _to_bool(kwargs.get("3", False), name="full_paths")

    iterator = root.rglob(pattern) if recursive else root.glob(pattern)
    out: list[str] = []
    for entry in iterator:
        if full_paths:
            out.append(str(entry.resolve()))
        else:
            out.append(entry.relative_to(root).as_posix())
    out.sort()
    return out


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="dir",
    namespace="default",
    kind="sequence",
    arity=AritySpec(min_args=1, max_args=4),
    attrs_schema={},
    planner=default_planner_factory("default.dir", kind="sequence"),
    kernel_name="default.dir",
    description="List directory entries with optional glob filtering",
)
