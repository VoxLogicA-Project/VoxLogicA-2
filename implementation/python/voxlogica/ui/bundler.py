"""Turn the UI source tree into a served bundle, without ever rebuilding twice.

The bundle is not a startup artefact. It is a *function of the source tree*,
memoised on a fingerprint of that tree, so the server can answer a request for
``app.js`` with fresh bytes at any moment without anybody having restarted it.

Two modes, auto-detected:

* **shipped** -- ``voxlogica/ui/static/app.js`` exists (the wheel carries a
  prebuilt bundle). Nothing is hashed, nothing is compiled, node is not needed.
* **dev** -- ``implementation/ui/`` is present next to the Python package.
  esbuild runs on demand; results land in a content-addressed cache directory,
  so switching back and forth between two edits (or between branches) reuses
  builds instead of recompiling them.

The fingerprint is a stat walk -- one ``stat()`` per source file, a few dozen
files here. It runs when a page is loaded, not on API traffic, and exists only
as a correctness net underneath the watcher (see :mod:`voxlogica.ui.watcher`):
a filesystem event can be missed -- atomic-rename editors, network mounts, a
watcher that started after the edit -- and then the watcher alone would happily
serve stale bytes forever. The stat walk makes the worst case "one page load
late" instead of "until someone restarts the server".
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that are outputs or dependencies, never inputs. Hashing
# node_modules would dominate the stat walk (tens of thousands of files) and
# tell us nothing package-lock.json does not already say.
_IGNORED_DIRS = {"node_modules", "dist", ".git", ".svelte-kit", "__pycache__"}

# Files outside src/ that still change the output.
_ROOT_INPUTS = ("index.html", "build.mjs", "package.json", "package-lock.json")

_BUILD_TIMEOUT = 180.0
_INSTALL_TIMEOUT = 600.0


class BundleError(RuntimeError):
    """A build failed. Carries the tool output so the UI can show it verbatim."""

    def __init__(self, summary: str, detail: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail


@dataclass(frozen=True)
class Bundle:
    """A directory holding ``app.js``/``app.css`` plus the ``index.html`` to serve."""

    directory: Path
    index_html: Path
    fingerprint: str


def _cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "voxlogica" / "ui-bundles"


#: Bundles to keep on disk. Every edit during a session mints a new one (a dev
#: bundle is ~1 MB with its inline source map), so without a bound an afternoon
#: of work would leave hundreds of megabytes behind. Keeping several means
#: undoing an edit, or hopping between branches, still hits the cache.
_CACHE_KEEP = 12


def _prune_cache(keep: int = _CACHE_KEEP) -> None:
    root = _cache_root()
    try:
        entries = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return
    for stale in sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def _package_static() -> Path:
    return Path(__file__).resolve().parent / "static"


def _source_root() -> Path | None:
    """Locate ``implementation/ui`` relative to the installed Python package.

    Only meaningful in a source checkout; a wheel has no such sibling and falls
    back to the shipped bundle.
    """
    # voxlogica/ui/bundler.py -> voxlogica/ui -> voxlogica -> python -> implementation
    candidate = Path(__file__).resolve().parents[3] / "ui"
    return candidate if (candidate / "build.mjs").is_file() else None


class Bundler:
    """Serves bundle bytes, compiling only when the sources actually changed."""

    def __init__(self, *, source_root: Path | None = None, dev: bool = True) -> None:
        self._source_root = source_root if source_root is not None else _source_root()
        self._dev = dev
        self._lock = threading.Lock()
        self._current: Bundle | None = None
        self._error: BundleError | None = None
        self._error_fingerprint = ""

    @property
    def source_root(self) -> Path | None:
        """The watched source tree, or ``None`` when serving a shipped bundle."""
        return self._source_root

    @property
    def is_dev(self) -> bool:
        return self._source_root is not None

    @property
    def error(self) -> BundleError | None:
        """The last build failure, if the current sources do not compile."""
        return self._error

    # ------------------------------------------------------------------ build

    def ensure(self, *, force: bool = False) -> Bundle:
        """Return a bundle matching the sources as they are right now.

        Raises :class:`BundleError` if they do not compile.
        """
        if not self.is_dev:
            static = _package_static()
            if not (static / "app.js").is_file():
                raise BundleError(
                    "No UI bundle available: this install carries no prebuilt "
                    "bundle and no implementation/ui source tree was found.")
            return Bundle(directory=static, index_html=static / "index.html", fingerprint="shipped")

        with self._lock:
            fingerprint = self._fingerprint()
            if (not force and self._current is not None
                    and self._current.fingerprint == fingerprint
                    # The cache is shared, and another process's pruning may have
                    # taken this directory out from under us. Cheaper to check
                    # than to serve a 404 for a bundle we think we have.
                    and (self._current.directory / "app.js").is_file()):
                # Only a failure recorded for *these* sources speaks about them.
                # Undoing a broken edit restores a fingerprint we have already
                # built, and must not be answered with the error the broken one
                # produced.
                if self._error is not None and self._error_fingerprint == fingerprint:
                    raise self._error
                return self._current
            # A failure recorded for exactly these sources is still the truth;
            # recompiling it would only reproduce the same diagnostics slowly.
            if not force and self._error is not None and self._error_fingerprint == fingerprint:
                raise self._error

            bundle = self._build(fingerprint)
            self._current = bundle
            self._error = None
            return bundle

    def invalidate(self) -> None:
        """Drop the memoised failure so the next :meth:`ensure` really rebuilds."""
        with self._lock:
            self._error = None

    def _build(self, fingerprint: str) -> Bundle:
        assert self._source_root is not None
        outdir = _cache_root() / fingerprint
        marker = outdir / "app.js"
        if marker.is_file():
            logger.debug("UI bundle cache hit: %s", outdir)
            return Bundle(directory=outdir, index_html=self._source_root / "index.html",
                          fingerprint=fingerprint)

        node, npm = self._toolchain()
        self._ensure_node_modules(npm)

        staging = outdir.with_name(outdir.name + f".tmp{os.getpid()}")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        cmd = [node, "build.mjs", "--outdir", str(staging)]
        if self._dev:
            cmd.append("--dev")
        try:
            proc = subprocess.run(cmd, cwd=self._source_root, capture_output=True,
                                  text=True, timeout=_BUILD_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleError(f"UI build timed out after {_BUILD_TIMEOUT:.0f}s", str(exc)) from exc
        if proc.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            error = BundleError("UI build failed", (proc.stderr or proc.stdout).strip())
            self._error = error
            self._error_fingerprint = fingerprint
            raise error

        # Publish atomically: a concurrent server (a second `voxlogica run`
        # sharing this cache) must never observe a half-written bundle.
        try:
            staging.rename(outdir)
        except OSError:
            if not marker.is_file():
                raise
            shutil.rmtree(staging, ignore_errors=True)
        _prune_cache()
        return Bundle(directory=outdir, index_html=self._source_root / "index.html",
                      fingerprint=fingerprint)

    def _toolchain(self) -> tuple[str, str]:
        """Node and npm: the system's, or a portable one fetched on first use.

        Nobody should have to install a JavaScript toolchain to run a program
        analysis tool. See toolchain.py.
        """
        from . import toolchain

        try:
            return toolchain.find()
        except toolchain.ToolchainError as exc:
            raise BundleError(
                "the UI could not be built because no usable Node toolchain is available",
                str(exc)) from exc

    def _ensure_node_modules(self, npm: str) -> None:
        """Install build dependencies when the manifest and the tree disagree.

        Keyed on the manifest rather than on "does node_modules exist", because
        the interesting case is not the empty tree -- it is a checkout whose
        node_modules predates a dependency that has since been added or bumped.
        That tree looks installed and fails the build with a raw esbuild
        "Could not resolve" that names the symptom and not the cause.
        """
        assert self._source_root is not None
        stamp = self._source_root / "node_modules" / ".voxlogica-install"
        manifest = self._manifest_digest()
        try:
            if stamp.read_text(encoding="utf-8").strip() == manifest:
                return
        except OSError:
            pass

        logger.info("Installing UI build dependencies...")
        proc = subprocess.run([npm, "install", "--no-audit", "--no-fund"],
                              cwd=self._source_root, capture_output=True, text=True,
                              timeout=_INSTALL_TIMEOUT)
        if proc.returncode != 0:
            raise BundleError("npm install failed for the UI build dependencies",
                              (proc.stderr or proc.stdout).strip())
        try:
            stamp.write_text(manifest, encoding="utf-8")
        except OSError:  # the install worked; a missing stamp only costs a retry
            logger.debug("could not write the UI dependency stamp", exc_info=True)

    def _manifest_digest(self) -> str:
        assert self._source_root is not None
        digest = hashlib.sha256()
        for name in ("package.json", "package-lock.json"):
            path = self._source_root / name
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"\0")
        return digest.hexdigest()

    # ------------------------------------------------------------ fingerprint

    def fingerprint(self) -> str:
        """Hash of the source tree as it is on disk right now (``""`` if shipped)."""
        return "shipped" if self._source_root is None else self._fingerprint()

    def _fingerprint(self) -> str:
        assert self._source_root is not None
        digest = hashlib.sha256()
        for path in sorted(self._input_files()):
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(self._source_root).as_posix()
            digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
        digest.update(b"dev" if self._dev else b"prod")
        return digest.hexdigest()[:32]

    def _input_files(self):
        assert self._source_root is not None
        for name in _ROOT_INPUTS:
            path = self._source_root / name
            if path.is_file():
                yield path
        src = self._source_root / "src"
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
            for filename in filenames:
                yield Path(dirpath) / filename
