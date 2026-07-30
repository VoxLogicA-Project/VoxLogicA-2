"""Turn run_scaling_suite.sh's raw output into the manuscript's tables.

Deliberately simple text parsing over the harness's own summary.txt, rather
than a shared structured format: the harness's output is meant to be read
directly too (that's why it's a flat, greppable log), and this script exists
only to save re-deriving the arithmetic (speedup, cpu/wall, work inflation)
by hand each time. See manuscripts/engine-scaling-2026-07.md for what these
numbers mean and doc/dev/scaling-test-design.md for why each column exists.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROW_RE = re.compile(
    r"(?P<label>\w+) workers=(?P<w>\d+) wall=(?P<wall>[\w.]+) "
    r"cpu=(?P<cpu>[\w./]+) bitmarker_present=(?P<ok>\d+)"
)


def parse_summary(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        m = ROW_RE.search(line)
        if m:
            d = m.groupdict()
            d["w"] = int(d["w"])
            try:
                d["wall"] = float(d["wall"])
            except ValueError:
                d["wall"] = None
            try:
                d["cpu"] = float(d["cpu"])
            except ValueError:
                d["cpu"] = None
            rows.append(d)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", type=Path)
    args = parser.parse_args()

    summary = args.results_dir / "summary.txt"
    if not summary.exists():
        raise SystemExit(f"no summary.txt in {args.results_dir} -- run run_scaling_suite.sh first")

    rows = parse_summary(summary)
    by_label: dict[str, list[dict]] = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r)

    for label, group in by_label.items():
        group.sort(key=lambda r: r["w"])
        serial = next((r["wall"] for r in group if r["w"] == 1), None)
        print(f"\n=== {label} ===")
        header = f"{'workers':>8} {'wall':>10} {'cpu':>10} {'speedup':>9} {'cpu/wall':>9}"
        print(header)
        for r in group:
            if r["wall"] is None:
                print(f"{r['w']:>8} {'FAIL':>10}")
                continue
            speedup = f"{serial / r['wall']:.2f}x" if serial else "?"
            cpu_wall = f"{r['cpu'] / r['wall']:.1f}" if r["cpu"] is not None else "n/a"
            print(f"{r['w']:>8} {r['wall']:>10.2f} "
                  f"{r['cpu'] if r['cpu'] is not None else 'n/a':>10} "
                  f"{speedup:>9} {cpu_wall:>9}")

    if not rows:
        print("No parseable rows found in summary.txt -- check the run completed "
              "(look for SUITE_DONE at the end of the file).")


if __name__ == "__main__":
    main()
