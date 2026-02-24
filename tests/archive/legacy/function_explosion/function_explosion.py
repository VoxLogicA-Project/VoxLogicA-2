#!/usr/bin/env python3
"""
Standalone test script for function explosion. Generates an .imgql file, runs the test, and prints results.
"""
import argparse
import tempfile
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
from tests.voxlogica_testinfra import run_imgql_test

# Set working directory to project root
os.chdir(repo_root)

description = """Combinatorial explosion test: generates a chain of function declarations in .imgql where each function calls its predecessors multiple times, causing a combinatorial increase in operations. Used to stress-test the reducer's handling of complex dependencies."""


def generate_function_explosion_imgql(depth: int, out_path: str):
    lines = [
        "// Function declaration based Fibonacci chain with combinatorial explosion",
        "// Each function calls its predecessors multiple times, causing combinatorial explosion",
        "",
        "// Base cases - using a dummy parameter 'x'",
        "let f0(x) = 1",
        "let f1(x) = 1",
        "",
        "// Functions calling predecessors",
    ]
    for i in range(2, depth + 1):
        prev1 = f"f{i-1}(x+1)"
        prev2 = f"f{i-2}(x-1)"
        prev3 = f"f{i-1}(x*2)"
        prev4 = f"f{i-2}(x/2)"
        prev5 = f"f{i-1}(x)"
        prev6 = f"f{i-2}(x)"
        lines.append(
            f"let f{i}(x) = {prev1} + {prev2} + {prev3} + {prev4} + {prev5} + {prev6}"
        )
    lines.append("")
    lines.append(f'print "function_explosion_f{depth}" f{depth}(1)')
    with open(out_path, "w") as f:
        f.write("\n".join(lines))


def main():
    print(f"\nTest Description: {description}\n")
    parser = argparse.ArgumentParser(
        description="Generate and test function_explosion.imgql"
    )
    parser.add_argument(
        "--depth", type=int, default=10, help="Depth of function explosion"
    )
    parser.add_argument(
        "--keep", action="store_true", help="Keep the generated .imgql file"
    )
    parser.add_argument(
        "--imgql-path",
        type=str,
        default=None,
        help="Path to write the .imgql file (default: temp file)",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    args = parser.parse_args()

    if args.imgql_path:
        imgql_path = args.imgql_path
    else:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".imgql")
        imgql_path = tmp.name
        tmp.close()

    generate_function_explosion_imgql(args.depth, imgql_path)
    print(f"Generated: {imgql_path}")
    result = run_imgql_test(imgql_path, language=args.language)
    if not result:
        sys.exit(1)

    if not args.keep and not args.imgql_path:
        os.remove(imgql_path)


if __name__ == "__main__":
    main()
