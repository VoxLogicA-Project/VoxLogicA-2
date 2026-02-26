#!/usr/bin/env python3
"""Fetch VoxLogicA-1 binary from GitHub releases when missing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests._vox1_binary import LEGACY_BIN_ENV, resolve_legacy_binary_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="Print only the resolved path")
    args = parser.parse_args()

    path = resolve_legacy_binary_path(auto_download=True)
    if path is None:
        if not args.quiet:
            print(
                f"Unable to resolve VoxLogicA-1 binary. "
                f"Set {LEGACY_BIN_ENV} or allow GitHub release downloads.",
                file=sys.stderr,
            )
        return 1

    if args.quiet:
        print(path)
    else:
        print(f"Resolved VoxLogicA-1 binary: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
