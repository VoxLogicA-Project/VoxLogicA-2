#!/usr/bin/env python3
import subprocess
import sys
import os
from pathlib import Path
import argparse


def run_python_tests():
    print("Running Python tests...")
    python_test_result = subprocess.run(
        [sys.executable, "-m", "unittest", "test_voxlogica.py"],
        cwd=os.path.dirname(__file__),
    )
    if python_test_result.returncode == 0:
        print("Python tests PASSED.")
    else:
        print("Python tests FAILED.")
    return python_test_result.returncode


def run_fsharp_test(imgql_file):
    print(f"\nRunning F# test ({imgql_file})...")
    fsharp_cmd = [
        "dotnet",
        "run",
        "--project",
        "../implementation/fsharp/VoxLogicA.fsproj",
        imgql_file,
    ]
    fsharp_result = subprocess.run(fsharp_cmd, cwd=os.path.dirname(__file__))
    if fsharp_result.returncode == 0:
        print(f"F# test {imgql_file} PASSED.")
    else:
        print(f"F# test {imgql_file} FAILED.")
    return fsharp_result.returncode


def main():
    parser = argparse.ArgumentParser(description="Run VoxLogicA tests.")
    parser.add_argument(
        "--language",
        choices=["python", "fsharp"],
        help="Run only tests for the specified language.",
    )
    parser.add_argument(
        "--file",
        help="Run only the test for the specified .imgql file (base name, no extension, F# only).",
    )
    args = parser.parse_args()

    failed = False

    # Python tests
    if args.language is None or args.language == "python":
        if args.file is None:
            failed = run_python_tests() != 0 or failed
        else:
            print("[INFO] --file is ignored for Python tests.")

    # F# tests
    if args.language is None or args.language == "fsharp":
        test_files = ["test.imgql", "fibonacci_chain.imgql", "function_explosion.imgql"]
        if args.file:
            imgql_file = (
                args.file if args.file.endswith(".imgql") else args.file + ".imgql"
            )
            if not Path(__file__).parent.joinpath(imgql_file).exists():
                print(
                    f"[ERROR] Test file {imgql_file} does not exist in tests directory."
                )
                sys.exit(1)
            failed = run_fsharp_test(imgql_file) != 0 or failed
        else:
            for f in test_files:
                failed = run_fsharp_test(f) != 0 or failed

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
