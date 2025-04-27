#!/usr/bin/env python3
import subprocess
import sys
import os
from pathlib import Path

# Run Python tests
print("Running Python tests...")
python_test_result = subprocess.run(
    [sys.executable, "-m", "unittest", "test_voxlogica.py"],
    cwd=os.path.dirname(__file__),
)

if python_test_result.returncode == 0:
    print("Python tests PASSED.")
else:
    print("Python tests FAILED.")

# Run F# tests
print("\nRunning F# tests...")
fsharp_cmd = [
    "dotnet",
    "run",
    "--project",
    "../implementation/fsharp/VoxLogicA.fsproj",
    "test.imgql",
]
fsharp_result = subprocess.run(fsharp_cmd, cwd=os.path.dirname(__file__))

if fsharp_result.returncode == 0:
    print("F# tests PASSED.")
else:
    print("F# tests FAILED.")

# Exit with nonzero code if any test failed
if python_test_result.returncode != 0 or fsharp_result.returncode != 0:
    sys.exit(1)
