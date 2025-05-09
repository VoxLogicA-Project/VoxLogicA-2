#!/usr/bin/env python3
import subprocess
import sys
import os
from pathlib import Path
import argparse
# from fibonacci_chain.fibonacci_chain import run_fibonacci_chain_test
# from function_explosion.function_explosion import run_function_explosion_test
from basic_test import test as basic_test_module
from fibonacci_chain import fibonacci_chain as fibonacci_chain_module
from function_explosion import function_explosion as function_explosion_module
from dag_equivalence import dag_equivalence as dag_equivalence_module

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

    print("Running basic test...")
    basic_test_module.main()
    print("Running fibonacci chain test...")
    fibonacci_chain_module.main()
    print("Running function explosion test...")
    function_explosion_module.main()
    print("Running DAG equivalence test...")
    dag_equivalence_module.main()


if __name__ == "__main__":
    main()
