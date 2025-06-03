#!/usr/bin/env python3
"""
Test suite for VoxLogicA DAG generation.

This test verifies that imgql programs can be parsed and transformed into DAGs correctly.
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
    """Return a list of valid and representative imgql program strings."""
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

def run_imgql_and_get_json(imgql_path: str) -> Dict:
    """
    Run the Python implementation on the given imgql file and return the resulting DAG as a JSON object.
    """
    exe = shutil.which("python3")
    venv_python = shutil.which("python", path="implementation/python/venv/bin")
    python_exec = venv_python or exe
    json_path = f"{imgql_path}.json"
    
    cmd = [
        python_exec, 
        "-m", 
        "voxlogica.main", 
        "run", 
        imgql_path, 
        "--save-task-graph-as-json", 
        json_path
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "implementation/python"
    
    try:
        subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
        with open(json_path) as f:
            return json.load(f)
    except subprocess.CalledProcessError as e:
        print(f"Error running imgql program: {e}")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        raise

def main():
    print("VoxLogicA DAG Generation Test")
    test_cases = generate_imgql_test_cases()
    
    for idx, imgql in enumerate(test_cases, 1):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".imgql") as tmp:
            tmp.write(imgql.encode("utf-8"))
            imgql_path = tmp.name
            
        try:
            print(f"\nTest case {idx}:")
            print("-" * 40)
            print("Input program:")
            print(imgql)
            print("\nGenerating DAG...")
            
            # Run the Python implementation
            try:
                result = run_imgql_and_get_json(imgql_path)
                print("✓ Successfully generated DAG")
                print(f"DAG contains {len(result.get('nodes', []))} nodes")
            except Exception as e:
                print(f"✗ Failed to generate DAG: {e}")
                continue
                
        finally:
            # Clean up temporary files
            if os.path.exists(imgql_path):
                os.remove(imgql_path)
            json_path = f"{imgql_path}.json"
            if os.path.exists(json_path):
                os.remove(json_path)
    
    print("\nTest completed.")

if __name__ == "__main__":
    main() 