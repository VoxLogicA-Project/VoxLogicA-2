#!/usr/bin/env python3
"""
Test for the enqueue primitive functionality.

This test verifies that a VoxLogicA primitive can enqueue another primitive
for execution, demonstrating the extensibility of the primitives system.
"""

import sys
import os
from pathlib import Path
import argparse

# Standard path setup for VoxLogicA imports
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from tests.voxlogica_testinfra import run_imgql_test

# MANDATORY: Description of the test
description = """Tests the enqueue primitive in the test namespace, which demonstrates
the capability of a VoxLogicA primitive to enqueue another primitive for execution.
This test validates that:
1. The enqueue primitive can be called successfully
2. It can schedule other primitives (fibonacci, timewaste) 
3. It can even enqueue itself recursively
4. The execution system handles the structured return values correctly
This serves as a proof-of-concept for dynamic task scheduling within primitives."""

def main():
    """Main test function."""
    print(f"\nTest Description: {description}\n")
    
    parser = argparse.ArgumentParser(description="Test enqueue primitive functionality")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    
    args = parser.parse_args()
    
    # Path to our test file
    test_file = repo_root / "test_enqueue.imgql"
    
    print("Testing enqueue primitive functionality...")
    print(f"Test file: {test_file}")
    
    # Run the test
    success = run_imgql_test(str(test_file), language=args.language)
    
    if success:
        print("✅ Enqueue primitive test passed!")
        print("The primitive successfully demonstrated the ability to enqueue other primitives.")
        return 0
    else:
        print("❌ Enqueue primitive test failed!")
        print("Check the output above for details.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
