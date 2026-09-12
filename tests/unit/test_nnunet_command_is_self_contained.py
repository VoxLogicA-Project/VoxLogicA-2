"""The app must find its own tools without an activated environment.

`nnunet_command` resolved a console script from `VIRTUAL_ENV`, then from PATH,
then gave up and returned the bare name. Both of those are set by `activate`
and by nothing else, so a run launched as

    /path/to/.venv/bin/python -m voxlogica.main run ...

— which is how every measured run on the test host is launched, and how every
non-login ssh command runs — had neither, and died with

    [Errno 2] No such file or directory: 'nnUNetv2_plan_and_preprocess'

while the executable sat in the very virtual environment that was running the
code. A console script installed by pip lives next to the interpreter it was
installed for, so `sys.executable` answers the question with no environment at
all.
"""

from __future__ import annotations

import os
import sys

import pytest

from voxlogica.primitives.nnunet import runtime


@pytest.mark.unit
def test_command_is_found_next_to_the_interpreter(tmp_path, monkeypatch) -> None:
    """No VIRTUAL_ENV, no PATH: the interpreter's own directory still answers."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "nnUNetv2_plan_and_preprocess"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", "")

    assert runtime.nnunet_command("nnUNetv2_plan_and_preprocess") == str(script)


@pytest.mark.unit
def test_an_activated_environment_still_wins(tmp_path, monkeypatch) -> None:
    """VIRTUAL_ENV keeps priority: an explicitly activated env is a choice."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    chosen = venv / "bin" / "nnUNetv2_train"
    chosen.write_text("#!/bin/sh\nexit 0\n")
    chosen.chmod(0o755)

    other = tmp_path / "other"
    other.mkdir()
    (other / "nnUNetv2_train").write_text("#!/bin/sh\nexit 0\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setattr(sys, "executable", str(other / "python"))

    assert runtime.nnunet_command("nnUNetv2_train") == str(chosen)


@pytest.mark.unit
def test_an_unknown_tool_still_degrades_to_its_name(tmp_path, monkeypatch) -> None:
    """Nothing found anywhere: return the bare name, as before, so the failure
    is the child process's and says which tool is missing."""
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert runtime.nnunet_command("nnUNetv2_nonexistent") == "nnUNetv2_nonexistent"
