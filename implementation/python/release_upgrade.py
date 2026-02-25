#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_REQ = REPO_ROOT / "implementation" / "python" / "requirements.txt"
TEST_REQ = REPO_ROOT / "implementation" / "python" / "requirements-test.txt"
BOOTSTRAP = REPO_ROOT / "bootstrap.py"
TEST_RUNNER = REPO_ROOT / "tests" / "run-tests.sh"


def _normalized_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _is_pinned_requirement(line: str) -> bool:
    if line.startswith(("-r ", "--requirement ")):
        return True
    if line.startswith(("-e ", "--editable ")):
        return False
    if line.startswith(("git+", "http://", "https://")):
        return False
    return "==" in line


def _validate_pinned_requirements() -> None:
    missing = [path for path in (RUNTIME_REQ, TEST_REQ) if not path.exists()]
    if missing:
        missing_csv = ", ".join(str(p) for p in missing)
        raise SystemExit(f"Missing requirement files: {missing_csv}")

    invalid: list[str] = []
    for req_file in (RUNTIME_REQ, TEST_REQ):
        for line in _normalized_lines(req_file):
            if not _is_pinned_requirement(line):
                invalid.append(f"{req_file}: {line}")

    if invalid:
        details = "\n".join(f" - {line}" for line in invalid)
        raise SystemExit(
            "Requirements are not deterministic. Every dependency line must be pinned with '=='.\n"
            f"{details}"
        )


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"+ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=REPO_ROOT, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Release helper: enforce pinned requirements, sync .venv, and run test suite."
    )
    parser.add_argument(
        "--python-version",
        default=None,
        help="Optional Python version passed through to bootstrap.py (otherwise uses .python-version).",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Only sync dependencies, do not run tests.",
    )
    parser.add_argument(
        "test_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to tests/run-tests.sh (use '-- <args>').",
    )
    args = parser.parse_args()

    _validate_pinned_requirements()

    bootstrap_cmd = [sys.executable, str(BOOTSTRAP), "--with-test", "--force"]
    if args.python_version:
        bootstrap_cmd.extend(["--python-version", args.python_version])
    _run(bootstrap_cmd)

    if args.skip_tests:
        print("Dependency sync complete (tests skipped).")
        return

    forwarded_args = list(args.test_args)
    if forwarded_args[:1] == ["--"]:
        forwarded_args = forwarded_args[1:]

    env = os.environ.copy()
    env["VOXLOGICA_SKIP_TEST_DEPS_INSTALL"] = "1"
    _run([str(TEST_RUNNER), *forwarded_args], env=env)
    print("Release environment upgrade complete.")


if __name__ == "__main__":
    main()
