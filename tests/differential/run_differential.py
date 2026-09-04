#!/usr/bin/env python3
"""Run the same computation on every VoxLogicA evaluator and compare the answers.

Nothing else in this repository does this. `perf/scaling/vl1_comparison` measures
how LONG VoxLogicA 1 and 2 take; it never asks whether they agree.

Engines are located by path and skipped when absent, so this is runnable with
whatever a machine happens to have. See README.md for what each one is and for
the one weakness of the kit: VoxLogicA 1 has its own syntax, so a case is a PAIR
of programs, and an A-vs-rest disagreement can be a mistranslation rather than a
divergence. B, C and D share a syntax; a disagreement among those is a bug.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE / "programs"
REPOS = Path(os.environ.get("VOXLOGICA_REPOS", Path.home() / "data/local/repos"))

#: label -> (kind, dialect, location). `kind` says how to invoke it; `dialect`
#: says which program file to feed it.
#:
#: THE THREE VoxLogicA 2 ENGINES ARE NOT SOURCE-COMPATIBLE, which this kit found
#: on its second case: `border` takes no arguments on `main` and one on
#: `incoming`. So a dialect can have its own program, and a case that does not
#: provide one falls back to `vl2`. That fallback is not a formality -- it is how
#: the kit reports which engines a case could actually reach.
ENGINES: list[tuple[str, str, str, Path]] = [
    ("A vl1", "vl1", "vl1", Path("/home/VoxLogicA/binaries/VoxLogicA_1.3.3-experimental_linux-x64/VoxLogicA")),
    ("B lazy", "vl2", "vl2main", REPOS / "vlx-main"),
    ("C engine", "vl2", "vl2", REPOS / "vlx-incoming"),
    ("D handles", "vl2", "vl2", REPOS / "vlx-handles"),
]

VENV = Path(os.environ.get("VOXLOGICA_VENV",
                           REPOS / "VoxLogicA-2/.venv/bin/python"))

#: A goal line, on either engine: `name=value`.
GOAL = re.compile(r"^\s*\[?[^\]]*\]?\s*\[?user\]?\s*(\w+)=(.+?)\s*$")


def goals_from(text: str) -> dict[str, str]:
    """The printed goals of a run, whichever engine printed them."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = re.sub(r"^\[\s*\d+ms\]\s*\[user\]\s*", "", line).strip()
        if "=" in stripped and not stripped.startswith(("[", "{", '"')):
            name, _, value = stripped.partition("=")
            if name and " " not in name.strip():
                found[name.strip()] = value.strip()
    return found


def run_vl1(binary: Path, program: Path) -> dict[str, str]:
    out = subprocess.run([str(binary), str(program)], capture_output=True,
                         text=True, timeout=900, cwd=binary.parent)
    return goals_from(out.stdout + out.stderr)


_FLAGS: dict[Path, list[str]] = {}


def vl2_flags(checkout: Path) -> list[str]:
    """The flags this checkout actually accepts.

    The four engines span a lot of history: `main` has no scheduling engine and
    so none of its switches. Asking `--help` once is cheaper than a matrix of
    version guesses, and it keeps a new flag from silently changing what the
    older engines are being compared under.
    """
    cached = _FLAGS.get(checkout)
    if cached is None:
        env = dict(os.environ, PYTHONPATH=str(checkout / "implementation/python"))
        helped = subprocess.run([str(VENV), "-m", "voxlogica.main", "run", "--help"],
                                capture_output=True, text=True, timeout=120,
                                cwd=checkout, env=env).stdout
        cached = [flag for flag in ("--no-serve", "--no-cache") if flag in helped]
        _FLAGS[checkout] = cached
    return cached


def run_vl2(checkout: Path, program: Path) -> dict[str, str]:
    env = dict(os.environ, PYTHONPATH=str(checkout / "implementation/python"))
    out = subprocess.run([str(VENV), "-m", "voxlogica.main", "run", str(program),
                          *vl2_flags(checkout)],
                         capture_output=True, text=True, timeout=900,
                         cwd=checkout, env=env)
    return goals_from(out.stdout + out.stderr)


def close(left: str, right: str, tolerance: float) -> bool:
    """Whether two printed values agree, numerically where they are numbers."""
    if left == right:
        return True
    try:
        return abs(float(left) - float(right)) <= tolerance
    except ValueError:
        pass
    numbers = (re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", left),
               re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", right))
    if len(numbers[0]) != len(numbers[1]) or not numbers[0]:
        return False
    return all(abs(float(a) - float(b)) <= tolerance
               for a, b in zip(*numbers))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="only these cases")
    parser.add_argument("--tolerance", type=float, default=1e-9,
                        help="numeric agreement, absolute (default 1e-9)")
    parser.add_argument("--flair", default="/home/VoxLogicA/datasets/"
                        "MICCAI_BraTS2020_TrainingData/BraTS20_Training_001/"
                        "BraTS20_Training_001_flair.nii.gz")
    args = parser.parse_args()

    available = []
    for label, kind, dialect, where in ENGINES:
        if where.exists():
            available.append((label, kind, dialect, where))
        else:
            print(f"skip {label}: not at {where}")
    if len(available) < 2:
        print("nothing to compare")
        return 1

    cases = sorted({p.name.split(".")[0] for p in PROGRAMS.glob("*.imgql")})
    if args.case:
        cases = [c for c in cases if c in args.case]

    failures = 0
    for case in cases:
        print(f"\n=== {case} ===")
        answers: dict[str, dict[str, str]] = {}
        for label, kind, dialect, where in available:
            program = PROGRAMS / f"{case}.{dialect}.imgql"
            if not program.is_file() and dialect != "vl1":
                program = PROGRAMS / f"{case}.vl2.imgql"   # the common dialect
            if not program.is_file():
                print(f"  {label:<10} no program for this dialect")
                continue
            suffix = program.name.split(".")[-2]
            text = program.read_text().replace("$FLAIR", args.flair)
            scratch = Path("/tmp") / f"diff_{case}_{suffix}.imgql"
            scratch.write_text(text)
            try:
                answers[label] = (run_vl1(where, scratch) if kind == "vl1"
                                  else run_vl2(where, scratch))
            except subprocess.TimeoutExpired:
                print(f"  {label:<10} TIMEOUT")
                failures += 1

        names = sorted({n for goals in answers.values() for n in goals})
        for name in names:
            values = {label: goals.get(name, "-") for label, goals in answers.items()}
            distinct = {v for v in values.values() if v != "-"}
            agree = len(distinct) <= 1 or all(
                close(a, b, args.tolerance)
                for a in distinct for b in distinct)
            mark = "ok " if agree else "DIFF"
            print(f"  {mark} {name}")
            for label in answers:
                print(f"       {label:<10} {values[label]}")
            if not agree:
                failures += 1

    print(f"\n{failures} disagreement(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
