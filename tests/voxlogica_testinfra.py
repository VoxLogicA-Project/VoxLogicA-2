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
    return ["python", "fsharp"]


def get_voxlogica_cmd(language, imgql_path):
    repo_root = Path(__file__).resolve().parent.parent
    if language == "python":
        return [
            sys.executable,
            "-m",
            "implementation.python.voxlogica.main",
            "run",
            str(imgql_path),
        ], repo_root
    elif language == "fsharp":
        return [
            str(
                repo_root / "implementation/fsharp/bin/Debug/net9.0/osx-arm64/VoxLogicA"
            ),
            str(imgql_path),
        ], repo_root
    else:
        raise ValueError(f"Unknown language: {language}")


def run_imgql_test(imgql_path: str, language: Optional[str] = None):
    """
    Run the given imgql file with the specified language, or all if language is None.
    Returns a dict with results for each language.
    """
    results = {}
    languages = [language] if language else get_supported_languages()
    for lang in languages:
        cmd, cwd = get_voxlogica_cmd(lang, imgql_path)
        print(f"\n--- Running VoxLogicA ({lang}) on {imgql_path} ---")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            print(proc.stdout)
            if proc.returncode != 0:
                print(f"[ERROR] {lang} failed with code {proc.returncode}")
                print(proc.stderr)
                results[lang] = False
            else:
                results[lang] = True
        except Exception as e:
            print(f"[EXCEPTION] {lang} failed: {e}")
            results[lang] = False
    return results
