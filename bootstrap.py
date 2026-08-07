#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile


REPO_ROOT = Path(__file__).resolve().parent
VENV_DIR = REPO_ROOT / ".venv"
UV_STATE_DIR = REPO_ROOT / ".cache" / "uv"
UV_PYTHON_INSTALL_DIR = UV_STATE_DIR / "python"
# uv downloaded by us lives inside the repo cache, so bootstrapping needs no
# root and no writes outside the checkout (see _install_uv).
UV_BIN_DIR = UV_STATE_DIR / "bin"
UV_RELEASE_BASE = "https://github.com/astral-sh/uv/releases"
RUNTIME_REQ = REPO_ROOT / "implementation" / "python" / "requirements.txt"
TEST_REQ = REPO_ROOT / "implementation" / "python" / "requirements-test.txt"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"
ENV_STAMP = VENV_DIR / ".voxlogica-env.json"
DEFAULT_PYTHON_VERSION = "3.14t"
# Free-threading is only available from 3.13; the engine requires it (hard
# cutover -- see doc/dev/gil-free-default-plan.md), so the floor moves with it.
MIN_SUPPORTED = (3, 13)


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _python_build(python_bin: str | Path) -> tuple[tuple[int, int, int], bool] | None:
    """Return ((major, minor, micro), is_freethreaded) for an interpreter.

    Both facts come from one subprocess call because they are always needed
    together: a free-threaded build reports the SAME sys.version_info as the
    GIL build of the same version, so the version alone cannot tell the two
    apart (this is trap T2 -- comparing versions only would silently keep a
    GIL venv when the pin asks for free-threading).
    """
    try:
        completed = subprocess.check_output(
            [
                str(python_bin),
                "-c",
                "import sys,sysconfig;"
                "print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}');"
                "print(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)",
            ],
            text=True,
        )
        version_line, gil_line = completed.strip().splitlines()[:2]
        parts = version_line.strip().split(".")
        if len(parts) != 3:
            return None
        version = (int(parts[0]), int(parts[1]), int(parts[2]))
        return version, gil_line.strip() == "1"
    except Exception:
        return None


def _parse_version_spec(value: str) -> tuple[tuple[int, ...], bool]:
    """Parse a python version spec, returning (numbers, freethreaded).

    Accepts the two spellings uv understands for a free-threaded build --
    a trailing 't' ("3.14t", "3.14.4t") and the long form
    ("3.14.4+freethreaded") -- because the previous parser called int() on
    every dot-separated component and therefore raised ValueError on BOTH
    (trap T1).
    """
    text = value.strip()
    freethreaded = False

    if text.endswith("+freethreaded"):
        freethreaded = True
        text = text[: -len("+freethreaded")]

    parts = text.split(".")
    if parts and parts[-1].endswith("t") and parts[-1][:-1].isdigit():
        freethreaded = True
        parts[-1] = parts[-1][:-1]

    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(
            f"Invalid version '{value}', expected <major>.<minor>[.<patch>] "
            "optionally suffixed 't' or '+freethreaded' (e.g. '3.14t')"
        )
    return tuple(int(part) for part in parts), freethreaded


def _normalize_version_spec(parts: tuple[int, ...], freethreaded: bool) -> str:
    """Render a spec for uv. uv accepts '3.14t' and '3.14.4t' alike."""
    return ".".join(str(p) for p in parts) + ("t" if freethreaded else "")


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


def _vendored_uv() -> Path:
    if os.name == "nt":
        return UV_BIN_DIR / "uv.exe"
    return UV_BIN_DIR / "uv"


def _uv_download_targets() -> list[str]:
    """Rust target triples to try, best first.

    A list rather than a single value because the libc flavour of a Linux box
    cannot be told apart reliably from Python (platform.libc_ver() reads the
    *interpreter's* build, and a musl host may still ship a glibc python); the
    gnu build is by far the common case, so try it and fall back to musl.
    """
    system = platform.system()
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine)
    if arch is None:
        return []
    if system == "Linux":
        return [f"uv-{arch}-unknown-linux-gnu", f"uv-{arch}-unknown-linux-musl"]
    if system == "Darwin":
        return [f"uv-{arch}-apple-darwin"]
    if system == "Windows":
        return [f"uv-{arch}-pc-windows-msvc"]
    return []


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https host
        return response.read()


def _extract_uv(archive: Path, dest_dir: Path) -> Path:
    """Pull just the uv executable out of a release archive."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    wanted = "uv.exe" if os.name == "nt" else "uv"
    with tempfile.TemporaryDirectory(dir=str(dest_dir)) as staging_str:
        staging = Path(staging_str)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                names = [n for n in zf.namelist() if Path(n).name == wanted]
                if not names:
                    raise SystemExit(f"No '{wanted}' inside {archive.name}")
                zf.extract(names[0], staging)
                extracted = staging / names[0]
        else:
            with tarfile.open(archive) as tf:
                members = [m for m in tf.getmembers() if m.isfile() and Path(m.name).name == wanted]
                if not members:
                    raise SystemExit(f"No '{wanted}' inside {archive.name}")
                member = members[0]
                # Flatten: the tarball nests the binary under uv-<target>/.
                member.name = wanted
                try:
                    tf.extract(member, staging, filter="data")
                except TypeError:  # filter= only exists from 3.12
                    tf.extract(member, staging)
                extracted = staging / wanted
        final = dest_dir / wanted
        extracted.chmod(0o755)
        os.replace(extracted, final)
    return final


def _install_uv() -> list[str] | None:
    """Fetch a private uv into .cache/uv/bin. Returns None if unavailable.

    Deliberately unprivileged: nothing is written outside the checkout, so a
    user without root (or without a writable ~/.local/bin) can still run
    ./voxlogica on a shared machine. Set VOXLOGICA_NO_UV_DOWNLOAD=1 to opt out
    (air-gapped hosts), or VOXLOGICA_UV_VERSION=x.y.z to pin a release instead
    of tracking the latest.
    """
    if os.environ.get("VOXLOGICA_NO_UV_DOWNLOAD", "").strip():
        return None

    targets = _uv_download_targets()
    if not targets:
        return None

    version = os.environ.get("VOXLOGICA_UV_VERSION", "").strip()
    base = f"{UV_RELEASE_BASE}/download/{version}" if version else f"{UV_RELEASE_BASE}/latest/download"
    suffix = ".zip" if os.name == "nt" else ".tar.gz"

    print("uv not found; downloading a private copy into .cache/uv/bin ...", file=sys.stderr)
    last_error: Exception | None = None
    for target in targets:
        url = f"{base}/{target}{suffix}"
        try:
            payload = _download(url)
            # Releases publish a .sha256 next to each asset; verifying it is
            # cheap and keeps a truncated or MITM'd download from being exec'd.
            expected = _download(f"{url}.sha256").decode("utf-8", "replace").split()[0].strip()
            actual = hashlib.sha256(payload).hexdigest()
            if expected and actual != expected:
                raise SystemExit(
                    f"Checksum mismatch for {url}: expected {expected}, got {actual}"
                )
        except SystemExit:
            raise
        except Exception as exc:  # try the next target triple
            last_error = exc
            continue

        UV_BIN_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(UV_BIN_DIR)) as tmp:
            archive = Path(tmp) / f"{target}{suffix}"
            archive.write_bytes(payload)
            uv_path = _extract_uv(archive, UV_BIN_DIR)
        try:
            subprocess.check_output([str(uv_path), "--version"], text=True, stderr=subprocess.STDOUT)
        except Exception as exc:
            last_error = exc
            continue
        print(f"Installed uv at {uv_path}", file=sys.stderr)
        return [str(uv_path)]

    print(f"Could not download uv automatically: {last_error}", file=sys.stderr)
    return None


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

    vendored = _vendored_uv()
    if vendored.exists():
        candidates.append([str(vendored)])

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

    installed = _install_uv()
    if installed is not None:
        return installed

    raise SystemExit(
        "uv is required for deterministic bootstrapping and could not be installed "
        "automatically. Install it once (https://docs.astral.sh/uv/) and retry, or point "
        "VOXLOGICA_UV at an existing binary."
    )


def _run_uv(uv_cmd: list[str], args: list[str], *, attempts: int = 1) -> None:
    UV_STATE_DIR.mkdir(parents=True, exist_ok=True)
    UV_PYTHON_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(UV_STATE_DIR))
    env.setdefault("UV_PYTHON_INSTALL_DIR", str(UV_PYTHON_INSTALL_DIR))
    # torch drags in the multi-hundred-MB nvidia-* CUDA wheels; uv's 30s default
    # per-request timeout is not enough for those on a slow or shared link, and
    # it fails the whole install rather than just that wheel.
    env.setdefault("UV_HTTP_TIMEOUT", "300")
    for attempt in range(1, attempts + 1):
        try:
            subprocess.check_call([*uv_cmd, *args], cwd=REPO_ROOT, env=env)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            # Whatever already landed stays in UV_CACHE_DIR, so a retry only
            # re-fetches what the interrupted run had not finished.
            print(
                f"uv failed (attempt {attempt}/{attempts}); retrying -- already-downloaded "
                "packages are cached.",
                file=sys.stderr,
            )


def _ensure_venv(
    uv_cmd: list[str],
    python_spec: str,
    parsed_target: tuple[int, ...],
    want_freethreaded: bool,
) -> tuple[Path, tuple[int, int, int]]:
    recreate = False
    venv_python = _venv_python()
    if venv_python.exists():
        current = _python_build(venv_python)
        if current is None:
            recreate = True
        else:
            current_version, current_ft = current
            if len(parsed_target) == 3:
                recreate = current_version != parsed_target
            else:
                recreate = current_version[:2] != parsed_target
            # A GIL venv and a free-threaded venv of the same version are
            # indistinguishable by version alone, so compare the build too or
            # an existing GIL .venv survives the cutover untouched (trap T2).
            recreate = recreate or current_ft != want_freethreaded
    else:
        recreate = True

    if recreate:
        _run_uv(uv_cmd, ["python", "install", "--no-bin", python_spec])
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        _run_uv(uv_cmd, ["venv", "--python", python_spec, str(VENV_DIR)])

    if not venv_python.exists():
        raise SystemExit(f"Failed to create virtual environment python at {venv_python}")

    built = _python_build(venv_python)
    if built is None:
        raise SystemExit(f"Failed to detect python version for {venv_python}")
    resolved, resolved_ft = built
    if not _is_supported(resolved):
        raise SystemExit(
            f"Unsupported Python {resolved[0]}.{resolved[1]}.{resolved[2]}; "
            f"minimum is {MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]}"
        )
    if want_freethreaded and not resolved_ft:
        raise SystemExit(
            f"Requested a free-threaded interpreter ('{python_spec}') but {venv_python} "
            "reports Py_GIL_DISABLED=0. uv may have resolved a GIL build; check "
            "'uv python list' for a '+freethreaded' entry."
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
    freethreaded: bool,
) -> None:
    if not RUNTIME_REQ.exists():
        raise SystemExit(f"Missing requirements file: {RUNTIME_REQ}")
    if include_test and not TEST_REQ.exists():
        raise SystemExit(f"Missing requirements file: {TEST_REQ}")

    runtime_hash = _file_sha256(RUNTIME_REQ)
    test_hash = _file_sha256(TEST_REQ) if include_test else ""
    stamp = _load_stamp()

    resolved_str = f"{resolved_version[0]}.{resolved_version[1]}.{resolved_version[2]}"
    # Free-threadedness is part of the environment identity: cp314t and cp314
    # wheels are different artifacts, so a build flip must invalidate the stamp
    # even when version and requirement hashes are unchanged.
    freethreaded_str = "1" if freethreaded else "0"
    runtime_current = (
        stamp.get("python_spec") == python_spec
        and stamp.get("python_resolved") == resolved_str
        and stamp.get("python_freethreaded") == freethreaded_str
        and stamp.get("runtime_sha256") == runtime_hash
    )
    test_current = stamp.get("test_sha256") == test_hash if include_test else True

    if not force and runtime_current and test_current:
        return

    install_args = ["pip", "install", "--python", str(venv_python)]
    if force:
        install_args.append("--reinstall")
    install_args.extend(["-r", str(RUNTIME_REQ)])
    if include_test:
        install_args.extend(["-r", str(TEST_REQ)])
    _run_uv(uv_cmd, install_args, attempts=3)

    _save_stamp(
        {
            "python_spec": python_spec,
            "python_resolved": resolved_str,
            "python_freethreaded": freethreaded_str,
            "runtime_sha256": runtime_hash,
            "test_sha256": test_hash if include_test else stamp.get("test_sha256", ""),
        }
    )
    print("Environment synchronized with pinned requirements.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/sync deterministic VoxLogicA virtualenv with uv.")
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="Install only implementation/python/requirements.txt and skip test dependencies.",
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

    parsed_target, want_freethreaded = _parse_version_spec(args.python_version)
    if not _is_supported(parsed_target):
        raise SystemExit(
            f"Unsupported target Python {args.python_version}; minimum is {MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]}"
        )
    # Hard cutover: the engine's parallelism depends on a free-threaded build,
    # so refuse a GIL spec here rather than build an environment that would
    # then be rejected by the ./voxlogica wrapper.
    if not want_freethreaded:
        raise SystemExit(
            f"VoxLogicA requires a free-threaded Python; '{args.python_version}' asks for a "
            "GIL build. Use a 't'-suffixed spec (e.g. '3.14t'). See "
            "doc/dev/gil-free-default-plan.md."
        )

    uv_cmd = _detect_uv(args.uv)
    normalized_target = _normalize_version_spec(parsed_target, want_freethreaded)
    venv_python, resolved = _ensure_venv(
        uv_cmd, normalized_target, parsed_target, want_freethreaded
    )
    _sync_requirements(
        uv_cmd,
        venv_python,
        include_test=not bool(args.runtime_only),
        force=bool(args.force),
        python_spec=normalized_target,
        resolved_version=resolved,
        freethreaded=want_freethreaded,
    )


if __name__ == "__main__":
    main()
