"""What a client may name, and why that depends on who a client can be.

Loopback-only means the client is the person at this machine, and the rule is
deliberately empty. The rule exists so that the day this listens on anything
else, the boundary changes in one place rather than in fifteen call sites.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.ui import guard


@pytest.fixture(autouse=True)
def _reset():
    guard.configure(local_only=True)
    guard._chosen.clear()
    yield
    guard.configure(local_only=True)
    guard._chosen.clear()


def test_local_means_no_boundary(tmp_path):
    """They can open it with any other program on the machine."""
    assert guard.allowed(tmp_path / "anything.imgql") is True
    assert guard.allowed("/etc/hosts") is True
    assert guard.roots() == []


def test_off_loopback_the_launch_directory_is_the_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXLOGICA_ROOT", str(tmp_path / "project"))
    (tmp_path / "project" / "inside").mkdir(parents=True)
    guard.configure(local_only=False)

    assert guard.allowed(tmp_path / "project" / "inside" / "study.imgql") is True
    assert guard.allowed(tmp_path / "elsewhere.imgql") is False
    with pytest.raises(guard.Refused, match="launch directory"):
        guard.permit(tmp_path / "elsewhere.imgql")


def test_a_symlink_out_of_the_tree_does_not_get_through(tmp_path, monkeypatch):
    """The oldest way through a check like this one."""
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.imgql"
    secret.write_text("let a = 1\n")
    (root / "shortcut.imgql").symlink_to(secret)
    monkeypatch.setenv("VOXLOGICA_ROOT", str(root))
    guard.configure(local_only=False)

    assert guard.allowed(root / "shortcut.imgql") is False


def test_choosing_a_folder_is_what_widens_the_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXLOGICA_ROOT", str(tmp_path / "project"))
    (tmp_path / "project").mkdir()
    guard.configure(local_only=False)
    elsewhere = tmp_path / "chosen"
    elsewhere.mkdir()

    assert guard.allowed(elsewhere / "study.imgql") is False
    guard.approve(elsewhere)
    assert guard.allowed(elsewhere / "study.imgql") is True


def test_system_dialogues_belong_to_whoever_is_at_the_machine():
    assert guard.may_open_dialogs() is True
    guard.configure(local_only=False)
    assert guard.may_open_dialogs() is False
