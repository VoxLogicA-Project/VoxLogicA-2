import sys
import os
import subprocess
from pathlib import Path
from typing import Optional

# Ensure implementation modules are importable regardless of cwd
repo_root = Path(__file__).resolve().parent.parent
py_impl = repo_root / "implementation" / "python"
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
if str(py_impl) not in sys.path:
    sys.path.insert(0, str(py_impl))

from implementation.python.voxlogica.parser import parse_program
from implementation.python.voxlogica.reducer import reduce_program


def get_supported_languages():
    return ["python"]


def get_voxlogica_cmd(language, imgql_path):
    repo_root = Path(__file__).resolve().parent.parent
    if language == "python":
        # Use the installed voxlogica command from the venv
        voxlogica_path = repo_root / ".venv" / "bin" / "voxlogica"
        return [
            str(voxlogica_path),
            "run",
            str(imgql_path),
        ], repo_root
    else:
        raise ValueError(f"Unknown language: {language}")


def run_imgql_test(imgql_path: str, language: Optional[str] = None):
    """
    Run the given imgql file with the specified language, or all if language is None.
    Returns True if all selected implementations pass, False if any fail.
    """
    results = {}
    languages = [language] if language else get_supported_languages()
    all_passed = True
    for lang in languages:
        cmd, cwd = get_voxlogica_cmd(lang, imgql_path)
        print(f"\n--- Running VoxLogicA ({lang}) on {imgql_path} ---")
        print(f"Command: {' '.join(cmd)}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            # Always print stdout if there is any
            if proc.stdout.strip():
                print("STDOUT:")
                print(proc.stdout)
            # Always print stderr if there is any
            if proc.stderr.strip():
                print("STDERR:")
                print(proc.stderr)

            if proc.returncode != 0:
                print(f"[ERROR] {lang} failed with return code {proc.returncode}")
                results[lang] = False
                all_passed = False
            else:
                print(f"[SUCCESS] {lang} completed successfully")
                results[lang] = True
        except Exception as e:
            print(f"[EXCEPTION] {lang} failed: {e}")
            results[lang] = False
            all_passed = False
    return all_passed
