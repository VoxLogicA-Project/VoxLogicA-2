#!/usr/bin/env python3
"""
Reproduction script for the F# stack overflow issue in VoxLogicA-2
"""

import os
import subprocess
import sys
from pathlib import Path


def main():
    # Get the repo root
    repo_root = (
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"])
        .decode("utf-8")
        .strip()
    )

    # Path to the test file
    test_file_path = os.path.join(
        repo_root,
        "META",
        "ISSUE",
        "ISSUE_1_fsharp_stack_overflow",
        "function_explosion_failure.imgql",
    )

    # Create a copy of the test file in the tests directory
    tests_dir = os.path.join(repo_root, "tests")
    test_file_copy = os.path.join(tests_dir, "function_explosion_failure.imgql")

    with open(test_file_path, "r") as src:
        with open(test_file_copy, "w") as dst:
            dst.write(src.read())

    print(f"Test file copied to {test_file_copy}")
    print("Running the test file with F# implementation...")

    # Run the F# implementation
    fsharp_cmd = [
        "dotnet",
        "run",
        "--project",
        os.path.join(repo_root, "implementation", "fsharp", "VoxLogicA.fsproj"),
        test_file_copy,
    ]

    try:
        subprocess.run(fsharp_cmd, cwd=tests_dir, check=True)
        print("The test completed successfully. No stack overflow occurred.")
    except subprocess.CalledProcessError as e:
        print(f"The test failed with exit code {e.returncode}.")
        print("This likely indicates a stack overflow occurred.")
    except Exception as e:
        print(f"An error occurred: {e}")

    # Clean up
    if os.path.exists(test_file_copy):
        os.remove(test_file_copy)
        print(f"Cleaned up: {test_file_copy}")


if __name__ == "__main__":
    main()
