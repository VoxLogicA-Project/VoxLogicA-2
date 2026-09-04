"""The front-end unit tests, run from the one command that says if the UI works.

`decorate` is the only part of the source surface that can be tested rather than
looked at: it is a pure function of text, and everything downstream of it is
pixels. Its tests are JavaScript, because it is JavaScript, and they run under
`node --test`, which ships with node and needs no dependency.

They are reached from here so that `pytest -k ui` remains the single answer to
"is the UI broken". A second command to remember is a command that gets
remembered right up until the afternoon it matters.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "implementation" / "ui"


def test_the_source_decorator_passes_its_own_tests():
    if not (UI / "test").is_dir():
        pytest.skip("no UI sources here (running from a wheel)")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the bundle is prebuilt in this tree")

    result = subprocess.run(
        [node, "--test", "test/*.test.js"],
        cwd=UI,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
