#!/usr/bin/env python3
"""
Reproduction script for the Python comment parsing issue in VoxLogicA-2
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
        "ISSUE_2_python_comment_parsing",
        "comment_parsing_failure.imgql",
    )

    # Create a copy of the test file in the tests directory
    tests_dir = os.path.join(repo_root, "tests")
    test_file_copy = os.path.join(tests_dir, "comment_parsing_failure.imgql")

    with open(test_file_path, "r") as src:
        with open(test_file_copy, "w") as dst:
            dst.write(src.read())

    print(f"Test file copied to {test_file_copy}")

    # Run a simple Python script that attempts to parse the file
    parse_script = os.path.join(tests_dir, "test_comment_parsing.py")

    with open(parse_script, "w") as f:
        f.write(
            """
import sys
import os
from pathlib import Path

# Add repository root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from implementation.python.voxlogica.parser import parse_program

def main():
    try:
        program = parse_program('comment_parsing_failure.imgql')
        print("Success! The file was parsed correctly.")
        print(f"Program has {len(program.commands)} commands.")
    except Exception as e:
        print(f"Error parsing the file: {e}")
        print("This confirms the comment parsing issue exists.")
        sys.exit(1)

if __name__ == "__main__":
    main()
"""
        )

    print("Running the test script...")

    try:
        subprocess.run([sys.executable, parse_script], cwd=tests_dir, check=True)
        print("The test completed successfully. No parsing errors occurred.")
        print("This suggests the comment parsing issue might have been fixed!")
    except subprocess.CalledProcessError as e:
        print(f"The test failed with exit code {e.returncode}.")
        print("This confirms the comment parsing issue exists.")
    except Exception as e:
        print(f"An error occurred: {e}")

    # Clean up
    if os.path.exists(test_file_copy):
        os.remove(test_file_copy)
        print(f"Cleaned up: {test_file_copy}")

    if os.path.exists(parse_script):
        os.remove(parse_script)
        print(f"Cleaned up: {parse_script}")


if __name__ == "__main__":
    main()
