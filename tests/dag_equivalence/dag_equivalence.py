#!/usr/bin/env python3
"""
Test that the same imgql program produces identical DAGs (as JSON) in both Python and F# ports, after normalization.
"""
import json
import tempfile
import os
import sys
from pathlib import Path
from typing import List, Dict
import subprocess
import shutil

# Ensure project root is in sys.path
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Set working directory to project root
os.chdir(repo_root)

def generate_imgql_test_cases() -> List[str]:
    """Return a list of 5 valid and representative imgql program strings."""
    return [
        # Simple arithmetic
        "let a = 1\nlet b = 2\nlet c = a + b\nprint \"sum\" c",
        # Function and application
        "let f(x) = x * x\nlet y = f(3)\nprint \"square\" y",
        # Nested let and arithmetic
        "let x = 5\nlet y = x + 2\nlet z = y * 3\nprint \"result\" z",
        # Use of constants and function
        "let pi = 3.14\nlet area(r) = pi * r * r\nlet a = area(2)\nprint \"area\" a",
        # Chained operations
        "let x = 1\nlet y = x + 1\nlet z = y + 1\nlet w = z + 1\nprint \"chain\" w",
    ]

def run_port_and_get_json(imgql_path: str, port: str) -> Dict:
    """
    Run the specified port (python or fsharp) on the given imgql file and return the resulting DAG as a JSON object.
    """
    if port == "python":
        exe = shutil.which("python3")
        venv_python = shutil.which("python", path="implementation/python/venv/bin")
        python_exec = venv_python or exe
        cmd = [python_exec, "-m", "voxlogica.main", "run", imgql_path, "--save-task-graph-as-json", imgql_path + ".py.json"]
        env = os.environ.copy()
        env["PYTHONPATH"] = "implementation/python"
        subprocess.run(cmd, check=True, env=env)
        with open(imgql_path + ".py.json") as f:
            return json.load(f)
    elif port == "fsharp":
        # Run from repo root, use dotnet run with -- and correct relative paths
        json_path = imgql_path + ".fs.json"
        cmd = [
            "dotnet", "run", "--project", "implementation/fsharp", "--", "--savetaskgraphasjson", json_path, imgql_path
        ]
        subprocess.run(cmd, check=True)
        with open(json_path) as f:
            return json.load(f)
    else:
        raise ValueError(f"Unknown port: {port}")

def normalize_json(obj: Dict) -> str:
    """Return a normalized JSON string (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def main():
    print("DAG Equivalence Test: Python vs F# ports (with JSON normalization)")
    test_cases = generate_imgql_test_cases()
    all_passed = True
    for idx, imgql in enumerate(test_cases):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".imgql") as tmp:
            tmp.write(imgql.encode("utf-8"))
            imgql_path = tmp.name
        try:
            py_json = run_port_and_get_json(imgql_path, "python")
            fs_json = run_port_and_get_json(imgql_path, "fsharp")
            py_norm = normalize_json(py_json)
            fs_norm = normalize_json(fs_json)
            print(f"Test case {idx+1}:")
            if py_norm == fs_norm:
                print("  PASS: Normalized JSON outputs are identical.")
            else:
                print("  FAIL: Normalized JSON outputs differ!")
                print("  Python:", py_norm)
                print("  F#    :", fs_norm)
                all_passed = False
        finally:
            os.remove(imgql_path)
    if all_passed:
        print("All DAG equivalence tests passed.")
    else:
        print("Some DAG equivalence tests failed.")

if __name__ == "__main__":
    main() 