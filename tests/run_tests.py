#!/usr/bin/env python3
import subprocess
import sys
import os
from pathlib import Path
import argparse

# Always resolve project root (repo root is parent of 'tests')
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent
os.chdir(repo_root)

LOGS_DIR = script_dir / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# List of test modules (as python -m targets)
TEST_MODULES = [
    "tests.basic_test.test",
    "tests.fibonacci_chain.fibonacci_chain",
    "tests.function_explosion.function_explosion",
]


def run_test(mod, args):
    log_file = LOGS_DIR / (mod.split(".")[-1] + ".log")
    cmd = [sys.executable, "-m", mod]
    if args.language:
        cmd += ["--language", args.language]
    print(f"\n=== Running {mod} ===")
    print(f"Logging output to: {log_file}")
    with open(log_file, "w") as f:
        process = subprocess.Popen(
            cmd, cwd=repo_root, stdout=f, stderr=subprocess.STDOUT, text=True
        )
        process.wait()
        if process.returncode == 0:
            return "PASSED", log_file
        elif process.returncode < 0:
            return "CRASHED", log_file
        else:
            return "FAILED", log_file


def main():
    parser = argparse.ArgumentParser(description="Run all VoxLogicA tests")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    args = parser.parse_args()

    summary = []
    for mod in TEST_MODULES:
        status, log_file = run_test(mod, args)
        summary.append((mod, status, log_file))

    print("\n=== Test Summary ===")
    for mod, status, log_file in summary:
        print(f"{mod}: {status} (log: {log_file})")
    n_passed = sum(1 for _, s, _ in summary if s == "PASSED")
    n_failed = sum(1 for _, s, _ in summary if s == "FAILED")
    n_crashed = sum(1 for _, s, _ in summary if s == "CRASHED")
    print(f"\n{n_passed} passed, {n_failed} failed, {n_crashed} crashed.")
    if n_failed or n_crashed:
        sys.exit(1)


if __name__ == "__main__":
    main()
