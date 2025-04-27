#!/usr/bin/env python3
"""
Standalone test script for the Fibonacci chain. Generates an .imgql file, runs the test, and prints results.
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


def generate_fibonacci_chain_imgql(depth: int, out_path: str):
    lines = ["// Fibonacci-like chain: f0 = 1, f1 = 1, fn = fn-1 + fn-2"]
    lines.append("let f0 = 1")
    lines.append("let f1 = 1")
    for i in range(2, depth + 1):
        lines.append(f"let f{i} = f{i-1} + f{i-2}")
    lines.append(f'print "fib{depth}" f{depth}')
    with open(out_path, "w") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="Generate and test fibonacci_chain.imgql"
    )
    parser.add_argument(
        "--depth", type=int, default=100, help="Depth of Fibonacci chain"
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

    generate_fibonacci_chain_imgql(args.depth, imgql_path)
    print(f"Generated: {imgql_path}")
    run_imgql_test(imgql_path, language=args.language)

    if not args.keep and not args.imgql_path:
        os.remove(imgql_path)


if __name__ == "__main__":
    main()
