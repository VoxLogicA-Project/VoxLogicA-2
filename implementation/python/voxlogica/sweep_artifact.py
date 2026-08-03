"""Extract reproducible parameter-sweep results from VoxLogicA print output."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PRINT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)=(.*)$")
CASE_RE = re.compile(r"^(g\d+|c\d+)_(score|argbest)$")


def _value(text: str) -> Any:
    """Parse printed scalars/sequences while retaining non-literal text."""
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        try:
            return float(text)
        except ValueError:
            return text


def parse_prints(text: str) -> dict[str, Any]:
    """Return the last value printed for each label in a PTY or plain log."""
    clean = ANSI_RE.sub("", text).replace("\r", "\n")
    parsed: dict[str, Any] = {}
    for line in clean.splitlines():
        match = PRINT_RE.fullmatch(line.strip())
        if match:
            parsed[match.group(1)] = _value(match.group(2))
    return parsed


def build_artifact(source: Path, text: str) -> dict[str, Any]:
    """Build the stable ``voxlogica/sweep-results/v1`` artifact."""
    prints = parse_prints(text)
    names = prints.get("sweep_parameter_names", [])
    cases: dict[str, dict[str, Any]] = {}
    selected: dict[str, Any] = {}
    for label, value in prints.items():
        match = CASE_RE.fullmatch(label)
        if match:
            case, kind = match.groups()
            cases.setdefault(case, {})[kind] = value
            selected[label] = value
        elif (
            label.startswith("sweep_")
            or label.endswith("_param_grid")
            or "_surface_" in label
        ):
            selected[label] = value

    if isinstance(names, (list, tuple)):
        for result in cases.values():
            argbest = result.get("argbest")
            if isinstance(argbest, (list, tuple)) and len(argbest) == len(names):
                result["parameters"] = dict(zip((str(name) for name in names), argbest, strict=True))

    return {
        "schema": "voxlogica/sweep-results/v1",
        "source": str(source),
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "cases": cases,
        "prints": selected,
    }


def write_artifact(source: Path, destination: Path) -> Path:
    """Parse ``source`` and write a formatted JSON artifact to ``destination``."""
    text = source.read_text(encoding="utf-8", errors="replace")
    artifact = build_artifact(source, text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="VoxLogicA output log")
    parser.add_argument("destination", nargs="?", type=Path, help="output JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = args.destination or args.source.with_suffix(".sweep.json")
    write_artifact(args.source, destination)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
