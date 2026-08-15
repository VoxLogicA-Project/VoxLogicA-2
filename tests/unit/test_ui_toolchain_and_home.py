"""Running VoxLogicA must not require installing a JavaScript toolchain first.

These cover the two things that decide whether a fresh machine can open the UI:
where a workspace lives, per platform, and how Node is obtained when there is
none. The download itself is exercised by the platform-slug and verification
tests rather than by fetching 50MB in CI; the end-to-end fetch is a manual check
documented in README ("Node").
"""

from __future__ import annotations

import hashlib
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from voxlogica.ui import home, toolchain


# ---------------------------------------------------------------- where things live


def test_each_platform_uses_its_own_conventional_directory():
    """Not a dotfile in $HOME: every platform already has an answer, and the
    user's backup and sync tools know about theirs and not about ours.

    Asserted as segments, because constructing a Windows path on a Mac raises --
    which is exactly why the decision is separate from building the path.
    """
    parts = home.data_home_parts
    assert parts("darwin", "posix", {}, "/Users/x") == (
        "/Users/x", "Library", "Application Support", "VoxLogicA")
    assert parts("linux", "posix", {}, "/home/x") == (
        "/home/x", ".local", "share", "voxlogica")
    assert parts("linux", "posix", {"XDG_DATA_HOME": "/data"}, "/home/x") == (
        "/data", "voxlogica")
    assert parts("win32", "nt", {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}, "C:\\Users\\x") == (
        r"C:\Users\x\AppData\Local", "VoxLogicA")
    assert parts("win32", "nt", {}, "C:/Users/x") == (
        "C:/Users/x", "AppData", "Local", "VoxLogicA")
    # And the override wins everywhere.
    for platform_name, os_name in (("darwin", "posix"), ("linux", "posix"), ("win32", "nt")):
        assert parts(platform_name, os_name, {"VOXLOGICA_HOME": "/elsewhere"}, "/home/x") == (
            "/elsewhere",)


def test_the_home_can_be_moved_wholesale(monkeypatch, tmp_path):
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path / "elsewhere"))
    assert home.data_home() == tmp_path / "elsewhere"
    assert home.workspaces() == tmp_path / "elsewhere" / "workspaces"


def test_a_scratch_is_a_folder_with_the_document_inside(monkeypatch, tmp_path):
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path))
    path = home.scratch_path()
    assert path.name == home.DOCUMENT
    assert path.parent.parent == home.workspaces()


def test_two_scratches_started_in_the_same_second_do_not_collide(monkeypatch, tmp_path):
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path))
    first = home.scratch_path()
    first.parent.mkdir(parents=True)
    second = home.scratch_path()
    assert first.parent != second.parent


def test_recent_lists_workspaces_newest_first(monkeypatch, tmp_path):
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path))
    for index, name in enumerate(("2026-01-01-000000", "2026-01-02-000000")):
        folder = home.workspaces() / name
        folder.mkdir(parents=True)
        document = folder / home.DOCUMENT
        document.write_text("let a = 1\n")
        os.utime(document, (1000 + index, 1000 + index))
    assert [path.parent.name for path in home.recent()] == [
        "2026-01-02-000000", "2026-01-01-000000",
    ]


# ------------------------------------------------------------------- the toolchain


def test_every_supported_platform_has_a_node_build(monkeypatch):
    """Portability is a property of this table, so the table is the test."""
    cases = {
        ("darwin", "posix", "arm64"): ("darwin-arm64", "tar.gz"),
        ("darwin", "posix", "x86_64"): ("darwin-x64", "tar.gz"),
        ("linux", "posix", "x86_64"): ("linux-x64", "tar.xz"),
        ("linux", "posix", "aarch64"): ("linux-arm64", "tar.xz"),
        ("win32", "nt", "AMD64"): ("win-x64", "zip"),
        ("win32", "nt", "ARM64"): ("win-arm64", "zip"),
    }
    for (platform_name, os_name, machine), expected in cases.items():
        monkeypatch.setattr(sys, "platform", platform_name)
        monkeypatch.setattr(os, "name", os_name)
        monkeypatch.setattr(toolchain.platform, "machine", lambda machine=machine: machine)
        assert toolchain._platform_slug() == expected


def test_an_architecture_with_no_official_build_says_so(monkeypatch):
    monkeypatch.setattr(toolchain.platform, "machine", lambda: "sparc64")
    with pytest.raises(toolchain.ToolchainError, match="architecture"):
        toolchain._platform_slug()


def test_windows_and_posix_look_for_the_executables_they_actually_have(monkeypatch):
    # Built before os.name is patched: pathlib decides its flavour from it, and
    # a WindowsPath cannot be instantiated here.
    root = Path("/opt/node")
    expected_bin = root / "bin"

    monkeypatch.setattr(os, "name", "nt")
    node, npm = toolchain._executables(root)
    assert (node.name, npm.name) == ("node.exe", "npm.cmd")
    assert node.parent == root

    monkeypatch.setattr(os, "name", "posix")
    node, npm = toolchain._executables(root)
    assert (node.name, npm.name) == ("node", "npm")
    assert node.parent == expected_bin


def test_a_download_whose_checksum_is_wrong_is_refused(monkeypatch, tmp_path):
    """An unverified toolchain is a supply chain nobody is watching."""
    payload = b"not really node"

    class _Response:
        def read(self, _size=-1):
            nonlocal payload
            chunk, payload = payload, b""
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(toolchain.urllib.request, "urlopen", lambda *a, **k: _Response())
    with pytest.raises(toolchain.ToolchainError, match="checksum"):
        toolchain._verified_download("https://example/node.tar.gz", "0" * 64, tmp_path)


def test_a_download_whose_checksum_matches_is_kept(monkeypatch, tmp_path):
    payload = b"pretend this is node"
    digest = hashlib.sha256(payload).hexdigest()

    class _Response:
        def __init__(self):
            self._left = payload

        def read(self, _size=-1):
            chunk, self._left = self._left, b""
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(toolchain.urllib.request, "urlopen", lambda *a, **k: _Response())
    archive = toolchain._verified_download("https://example/node.tar.gz", digest, tmp_path)
    assert archive.read_bytes() == payload


def test_a_missing_checksum_line_is_an_error_not_a_shrug(monkeypatch):
    class _Response:
        def read(self):
            return b"deadbeef  node-v1.2.3-other-platform.tar.gz\n"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(toolchain.urllib.request, "urlopen", lambda *a, **k: _Response())
    with pytest.raises(toolchain.ToolchainError, match="published checksums"):
        toolchain._published_digest("node-v1.2.3-darwin-arm64.tar.gz", "1.2.3")


def test_unpacking_finds_the_single_root_of_either_archive_format(tmp_path):
    for maker, name in ((_tar, "node.tar.gz"), (_zip, "node.zip")):
        archive = maker(tmp_path / name)
        into = tmp_path / f"out-{name}"
        into.mkdir()
        root = toolchain._unpack(archive, into)
        assert root.name == "node-v1-fake"
        assert (root / "bin" / "node").exists()


def _tar(path: Path) -> Path:
    payload = path.parent / "payload"
    (payload / "node-v1-fake" / "bin").mkdir(parents=True, exist_ok=True)
    (payload / "node-v1-fake" / "bin" / "node").write_text("#!/bin/sh\n")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(payload / "node-v1-fake", arcname="node-v1-fake")
    return path


def _zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("node-v1-fake/bin/node", "#!/bin/sh\n")
    return path


def test_a_refused_download_names_what_to_install(monkeypatch):
    monkeypatch.setenv("VOXLOGICA_NO_NODE_DOWNLOAD", "1")
    monkeypatch.delenv("VOXLOGICA_NODE", raising=False)
    monkeypatch.setattr(toolchain.shutil, "which", lambda _name: None)
    with pytest.raises(toolchain.ToolchainError, match="Install Node"):
        toolchain.find()


def test_a_usable_node_on_path_is_used_as_is(monkeypatch):
    monkeypatch.delenv("VOXLOGICA_NODE", raising=False)
    monkeypatch.setattr(toolchain.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in ("node", "npm") else None)
    monkeypatch.setattr(toolchain, "_major", lambda _node: 22)
    assert toolchain.find() == ("/usr/bin/node", "/usr/bin/npm")


def test_a_node_too_old_for_the_build_is_replaced(monkeypatch, tmp_path):
    """A Node that cannot compile the UI is not a Node, for this purpose."""
    monkeypatch.delenv("VOXLOGICA_NODE", raising=False)
    monkeypatch.delenv("VOXLOGICA_NO_NODE_DOWNLOAD", raising=False)
    monkeypatch.setattr(toolchain.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in ("node", "npm") else None)
    monkeypatch.setattr(toolchain, "_major", lambda _node: 14)
    fetched = tmp_path / "node-v22"
    (fetched / "bin").mkdir(parents=True)
    (fetched / "bin" / "node").write_text("#!/bin/sh\n")
    (fetched / "bin" / "npm").write_text("#!/bin/sh\n")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(toolchain, "download", lambda *a, **k: fetched)
    node, _npm = toolchain.find()
    assert node == str(fetched / "bin" / "node")
