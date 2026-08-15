"""Node, without asking anyone to install Node.

Building the UI needs a JavaScript toolchain. Requiring the user to have one is
the difference between "run VoxLogicA" and "run VoxLogicA, after you have
installed Node, and after you have the right major version" -- and that second
sentence is where people stop. So if a usable Node is not on PATH, one is
fetched: the official build for this platform, unpacked into the application's
own data directory, used from there and never installed system-wide.

Three properties are deliberate:

* **It is opt-out, not opt-in.** VOXLOGICA_NODE points at a node binary to use
  instead; VOXLOGICA_NO_NODE_DOWNLOAD refuses the download and fails with a
  message that says what to install.
* **It is verified.** Node publishes SHASUMS256.txt beside the archives; the
  download is checked against it before anything is unpacked. An unverified
  toolchain is a supply chain nobody is watching.
* **It is per-version and atomic.** Each version unpacks into its own directory
  through a staging rename, so two processes racing to bootstrap cannot produce
  a half-extracted toolchain, and upgrading is a different directory rather
  than an in-place mutation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Pinned. A toolchain that floats is a build that differs between two machines
#: for a reason nobody can see. Bump deliberately.
NODE_VERSION = "22.11.0"

_BASE_URL = "https://nodejs.org/dist"
_DOWNLOAD_TIMEOUT = 300.0
#: The oldest Node that can run the build: esbuild and the Svelte compiler both
#: need modern ESM support.
_MINIMUM_MAJOR = 18


class ToolchainError(RuntimeError):
    """No usable Node, and none could be fetched."""


def _data_home() -> Path:
    from .home import data_home

    return data_home()


def _platform_slug() -> tuple[str, str]:
    """(node's name for this platform, archive extension)."""
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x64", "amd64": "x64",
        "arm64": "arm64", "aarch64": "arm64",
        "armv7l": "armv7l",
    }.get(machine)
    if arch is None:
        raise ToolchainError(f"no official Node build for this architecture ({machine})")
    if sys.platform == "darwin":
        return f"darwin-{arch}", "tar.gz"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}", "tar.xz"
    if os.name == "nt":
        return f"win-{arch}", "zip"
    raise ToolchainError(f"no official Node build for this platform ({sys.platform})")


def _major(node: str) -> int | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip().lstrip("v").split(".")[0])
    except (ValueError, IndexError):
        return None


def _bin_dir(root: Path) -> Path:
    # Windows puts node.exe and npm.cmd at the top level; everyone else uses bin/.
    return root if os.name == "nt" else root / "bin"


def _executables(root: Path) -> tuple[Path, Path]:
    directory = _bin_dir(root)
    if os.name == "nt":
        return directory / "node.exe", directory / "npm.cmd"
    return directory / "node", directory / "npm"


def _verified_download(url: str, expected: str, into: Path) -> Path:
    """Download `url` to `into`, refusing anything whose digest is not `expected`."""
    archive = into / url.rsplit("/", 1)[-1]
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response, archive.open("wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ToolchainError(
            f"the Node download did not match its published checksum\n"
            f"  expected {expected}\n  got      {actual}")
    return archive


def _published_digest(name: str, version: str) -> str:
    url = f"{_BASE_URL}/v{version}/SHASUMS256.txt"
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response:
        for line in response.read().decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == name:
                return parts[0]
    raise ToolchainError(f"{name} is not listed in Node's published checksums for v{version}")


def _unpack(archive: Path, into: Path) -> Path:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(into)
    else:
        with tarfile.open(archive) as tf:
            # `data` refuses absolute paths, parent traversal, links and device
            # files: an archive is untrusted input even when it is official.
            try:
                tf.extractall(into, filter="data")
            except TypeError:  # Python < 3.12 has no filter argument
                tf.extractall(into)
    roots = [child for child in into.iterdir() if child.is_dir()]
    if len(roots) != 1:
        raise ToolchainError(f"unexpected Node archive layout: {[r.name for r in roots]}")
    return roots[0]


def download(version: str = NODE_VERSION, *, into: Path | None = None) -> Path:
    """Fetch and unpack a portable Node. Returns its root directory."""
    slug, extension = _platform_slug()
    name = f"node-v{version}-{slug}.{extension}"
    url = f"{_BASE_URL}/v{version}/{name}"
    root = (into or _data_home() / "toolchain") / f"node-v{version}-{slug}"
    if _executables(root)[0].exists():
        return root

    root.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Fetching Node v%s for %s (one time, into %s)", version, slug, root.parent)
    expected = _published_digest(name, version)
    with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
        staging = Path(tmp)
        archive = _verified_download(url, expected, staging)
        unpacked = _unpack(archive, staging / "x")
        try:
            unpacked.rename(root)
        except OSError:
            # Another process won the race; theirs is as good as ours.
            if not _executables(root)[0].exists():
                raise
    return root


def find(*, allow_download: bool = True) -> tuple[str, str]:
    """`(node, npm)`, from PATH if usable, otherwise from a fetched toolchain.

    Raises ToolchainError with something actionable if neither is possible.
    """
    override = os.environ.get("VOXLOGICA_NODE")
    if override:
        node = Path(override).expanduser()
        npm = _executables(node.parent.parent if node.parent.name == "bin" else node.parent)[1]
        return str(node), str(npm if npm.exists() else shutil.which("npm") or npm)

    node = shutil.which("node")
    if node is not None:
        major = _major(node)
        npm = shutil.which("npm")
        if major is not None and major >= _MINIMUM_MAJOR and npm:
            return node, npm
        logger.info(
            "the Node on PATH is %s; fetching a supported one",
            f"v{major}" if major else "not usable")

    if not allow_download or os.environ.get("VOXLOGICA_NO_NODE_DOWNLOAD"):
        raise ToolchainError(
            f"the UI needs Node {_MINIMUM_MAJOR}+ and downloads are disabled. "
            f"Install Node, or point VOXLOGICA_NODE at a node binary.")

    root = download()
    node_path, npm_path = _executables(root)
    if not node_path.exists():
        raise ToolchainError(f"the fetched toolchain has no node at {node_path}")
    return str(node_path), str(npm_path)
