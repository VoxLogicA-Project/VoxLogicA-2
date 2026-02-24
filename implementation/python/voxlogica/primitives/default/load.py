"""Dataset loader primitive fallback."""

from __future__ import annotations

from pathlib import Path
import json


def execute(**kwargs):
    """Load dataset from path at runtime.

    Supported forms:
    - list/tuple input: returned as-is
    - .json: parsed JSON
    - .txt/.csv: list of stripped lines
    - other files: raw bytes
    """
    if "0" not in kwargs:
        raise ValueError("load requires dataset argument at key '0'")

    dataset = kwargs["0"]
    if isinstance(dataset, (list, tuple)):
        return list(dataset)

    path = Path(str(dataset))
    if not path.exists():
        raise ValueError(f"load source not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix in {".txt", ".csv"}:
        return [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines()]

    return path.read_bytes()
