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

TEST_MODULES = [
    "tests.basic_test.test",
    "tests.fibonacci_chain.fibonacci_chain",
    "tests.function_explosion.function_explosion",
]


def main():
    parser = argparse.ArgumentParser(description="Run all VoxLogicA tests")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    args = parser.parse_args()

    failures = 0
    for mod in TEST_MODULES:
        print(f"\n=== Running {mod} ===")
        cmd = [sys.executable, "-m", mod]
        if args.language:
            cmd += ["--language", args.language]
        result = subprocess.run(cmd, cwd=repo_root)
        if result.returncode != 0:
            print(f"FAILED: {mod}")
            failures += 1
        else:
            print(f"PASSED: {mod}")

    if failures:
        print(f"\n{failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")


if __name__ == "__main__":
    main()
