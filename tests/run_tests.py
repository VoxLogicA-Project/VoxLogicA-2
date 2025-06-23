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
    "tests.test_sha256_memoization.test_sha256_memoization",
    "tests.test_sha256_memoization_advanced.test_sha256_memoization_advanced",
    "tests.test_sha256_json_export.test_sha256_json_export",
    "tests.test_dag_dict_args.test_dag_dict_args",
    "tests.features.test_run_feature",
    "tests.features.test_version_feature",
    "tests.test_auto_cleanup.test_auto_cleanup",
    "tests.test_crash_behavior.test_crash_behavior",
    "tests.test_db_storage.test_db_storage", 
    "tests.test_simpleitk_direct.test_simpleitk_direct",
    "tests.test_enqueue_primitive.test_enqueue_primitive",
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
    ICONS = {"PASSED": "\u2705", "FAILED": "\u274c", "CRASHED": "\u26a0\ufe0f"}
    COLOR = {
        "PASSED": "\033[92m",
        "FAILED": "\033[91m",
        "CRASHED": "\033[93m",
        "END": "\033[0m",
    }
    for mod, status, log_file in summary:
        icon = ICONS.get(status, "?")
        color = COLOR.get(status, "")
        endc = COLOR["END"] if color else ""
        print(f"{color}{icon} {mod}: {status}{endc} (log: {log_file})")
    n_passed = sum(1 for _, s, _ in summary if s == "PASSED")
    n_failed = sum(1 for _, s, _ in summary if s == "FAILED")
    n_crashed = sum(1 for _, s, _ in summary if s == "CRASHED")
    print(
        f"\n{COLOR['PASSED']}{n_passed} passed{COLOR['END']}, {COLOR['FAILED']}{n_failed} failed{COLOR['END']}, {COLOR['CRASHED']}{n_crashed} crashed{COLOR['END']}."
    )
    if n_failed or n_crashed:
        sys.exit(1)


if __name__ == "__main__":
    main()
