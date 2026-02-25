#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent
VENV_DIR = REPO_ROOT / ".venv"
RUNTIME_REQ = REPO_ROOT / "implementation" / "python" / "requirements.txt"
TEST_REQ = REPO_ROOT / "implementation" / "python" / "requirements-test.txt"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"
ENV_STAMP = VENV_DIR / ".voxlogica-env.json"
DEFAULT_PYTHON_VERSION = "3.12.8"
MIN_SUPPORTED = (3, 11)


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _python_version(python_bin: str | Path) -> tuple[int, int, int] | None:
    try:
        completed = subprocess.check_output(
            [str(python_bin), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')"],
            text=True,
        )
        parts = completed.strip().split(".")
        if len(parts) != 3:
            return None
        return int(parts[0]), int(parts[1]), int(parts[2])
    except Exception:
        return None


def _parse_version_spec(value: str) -> tuple[int, int] | tuple[int, int, int]:
    parts = value.strip().split(".")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid version '{value}', expected <major>.<minor>[.<patch>]")
    numbers = tuple(int(part) for part in parts)
    return numbers  # type: ignore[return-value]


def _normalize_version_spec(parts: tuple[int, int] | tuple[int, int, int]) -> str:
    return ".".join(str(p) for p in parts)


def _is_supported(parts: tuple[int, int] | tuple[int, int, int]) -> bool:
    return (parts[0], parts[1]) >= MIN_SUPPORTED


def _default_python_spec() -> str:
    from_env = os.environ.get("VOXLOGICA_PYTHON_VERSION", "").strip()
    if from_env:
        return from_env
    if PYTHON_VERSION_FILE.exists():
        content = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()
        if content:
            return content
    return DEFAULT_PYTHON_VERSION


def _file_sha256(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def _load_stamp() -> dict[str, str]:
    if not ENV_STAMP.exists():
        return {}
    try:
        payload = json.loads(ENV_STAMP.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return {str(k): str(v) for k, v in payload.items()}
    return {}


def _save_stamp(payload: dict[str, str]) -> None:
    ENV_STAMP.parent.mkdir(parents=True, exist_ok=True)
    ENV_STAMP.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _detect_uv(explicit: str | None) -> list[str]:
    candidates: list[list[str]] = []
    if explicit:
        candidates.append([explicit])

    env_uv = os.environ.get("VOXLOGICA_UV", "").strip()
    if env_uv:
        candidates.append([env_uv])

    uv_bin = shutil.which("uv")
    if uv_bin:
        candidates.append([uv_bin])

    candidates.append([sys.executable, "-m", "uv"])

    seen: set[str] = set()
    for cmd in candidates:
        key = "\0".join(cmd)
        if key in seen:
            continue
        seen.add(key)
        try:
            subprocess.check_output([*cmd, "--version"], text=True, stderr=subprocess.STDOUT)
            return cmd
        except Exception:
            continue

    raise SystemExit(
        "uv is required for deterministic bootstrapping. Install it once (https://docs.astral.sh/uv/) and retry."
    )


def _run_uv(uv_cmd: list[str], args: list[str]) -> None:
    subprocess.check_call([*uv_cmd, *args], cwd=REPO_ROOT)


def _ensure_venv(
    uv_cmd: list[str],
    python_spec: str,
    parsed_target: tuple[int, int] | tuple[int, int, int],
) -> tuple[Path, tuple[int, int, int]]:
    _run_uv(uv_cmd, ["python", "install", python_spec])

    recreate = False
    venv_python = _venv_python()
    if venv_python.exists():
        current = _python_version(venv_python)
        if current is None:
            recreate = True
        else:
            if len(parsed_target) == 3:
                recreate = current != parsed_target
            else:
                recreate = current[:2] != parsed_target
    else:
        recreate = True

    if recreate:
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        _run_uv(uv_cmd, ["venv", "--python", python_spec, str(VENV_DIR)])

    if not venv_python.exists():
        raise SystemExit(f"Failed to create virtual environment python at {venv_python}")

    resolved = _python_version(venv_python)
    if resolved is None:
        raise SystemExit(f"Failed to detect python version for {venv_python}")
    if not _is_supported(resolved):
        raise SystemExit(
            f"Unsupported Python {resolved[0]}.{resolved[1]}.{resolved[2]}; "
            f"minimum is {MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]}"
        )

    return venv_python, resolved


def _sync_requirements(
    uv_cmd: list[str],
    venv_python: Path,
    *,
    include_test: bool,
    force: bool,
    python_spec: str,
    resolved_version: tuple[int, int, int],
) -> None:
    if not RUNTIME_REQ.exists():
        raise SystemExit(f"Missing requirements file: {RUNTIME_REQ}")
    if include_test and not TEST_REQ.exists():
        raise SystemExit(f"Missing requirements file: {TEST_REQ}")

    runtime_hash = _file_sha256(RUNTIME_REQ)
    test_hash = _file_sha256(TEST_REQ) if include_test else ""
    stamp = _load_stamp()

    resolved_str = f"{resolved_version[0]}.{resolved_version[1]}.{resolved_version[2]}"
    runtime_current = (
        stamp.get("python_spec") == python_spec
        and stamp.get("python_resolved") == resolved_str
        and stamp.get("runtime_sha256") == runtime_hash
    )
    test_current = stamp.get("test_sha256") == test_hash if include_test else True

    if not force and runtime_current and test_current:
        print("Environment already synchronized with pinned requirements.")
        return

    install_args = ["pip", "install", "--python", str(venv_python)]
    if force:
        install_args.append("--reinstall")
    install_args.extend(["-r", str(RUNTIME_REQ)])
    if include_test:
        install_args.extend(["-r", str(TEST_REQ)])
    _run_uv(uv_cmd, install_args)

    _save_stamp(
        {
            "python_spec": python_spec,
            "python_resolved": resolved_str,
            "runtime_sha256": runtime_hash,
            "test_sha256": test_hash if include_test else stamp.get("test_sha256", ""),
        }
    )
    print("Environment synchronized with pinned requirements.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/sync deterministic VoxLogicA virtualenv with uv.")
    parser.add_argument(
        "--with-test",
        action="store_true",
        help="Also install implementation/python/requirements-test.txt",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if requirement hashes are unchanged.",
    )
    parser.add_argument(
        "--python-version",
        default=_default_python_spec(),
        help="Target Python version (major.minor or major.minor.patch). Defaults to .python-version or 3.12.8.",
    )
    parser.add_argument(
        "--uv",
        default=None,
        help="Explicit uv binary path/name.",
    )
    args = parser.parse_args()

    parsed_target = _parse_version_spec(args.python_version)
    if not _is_supported(parsed_target):
        raise SystemExit(
            f"Unsupported target Python {args.python_version}; minimum is {MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]}"
        )

    uv_cmd = _detect_uv(args.uv)
    normalized_target = _normalize_version_spec(parsed_target)
    venv_python, resolved = _ensure_venv(uv_cmd, normalized_target, parsed_target)
    _sync_requirements(
        uv_cmd,
        venv_python,
        include_test=bool(args.with_test),
        force=bool(args.force),
        python_spec=normalized_target,
        resolved_version=resolved,
    )


if __name__ == "__main__":
    main()
