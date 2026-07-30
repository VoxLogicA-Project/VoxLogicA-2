"""engine/itk_threads.py: mechanical application of an ITK thread count.

This module makes NO decision about what count is right -- a previous version
did (`cores // workers`), was measured up to 3.1x slower than leaving ITK
alone, and was reverted (see manuscripts/engine-scaling-2026-07.md Part I sec
5). It only applies a value someone else -- engine/calibration.py's sweep, or
the engine reading a cached result -- decided on. These tests pin exactly that:
mechanical application, never a formula.
"""

from __future__ import annotations

import os
import sys

import pytest

from voxlogica.engine.itk_threads import ITK_THREADS_ENV, apply_itk_threads


@pytest.mark.unit
def test_sets_the_env_var_to_exactly_the_given_value(monkeypatch):
    monkeypatch.delenv(ITK_THREADS_ENV, raising=False)
    apply_itk_threads(5)
    assert os.environ.get(ITK_THREADS_ENV) == "5"


@pytest.mark.unit
def test_overwrites_any_previous_value_unconditionally(monkeypatch):
    """Unlike a formula that might defer to an existing setting, this module
    is the single point where a decided value takes effect -- it must not
    silently keep a stale one."""
    monkeypatch.setenv(ITK_THREADS_ENV, "999")
    apply_itk_threads(2)
    assert os.environ.get(ITK_THREADS_ENV) == "2"


@pytest.mark.unit
def test_calls_the_api_when_simpleitk_is_already_imported(monkeypatch):
    seen: list[int] = []

    class _ProcessObject:
        @staticmethod
        def SetGlobalDefaultNumberOfThreads(n: int) -> None:
            seen.append(n)

    class _FakeSimpleITK:
        ProcessObject = _ProcessObject

    monkeypatch.setitem(sys.modules, "SimpleITK", _FakeSimpleITK)
    apply_itk_threads(7)
    assert seen == [7]


@pytest.mark.unit
def test_does_not_import_simpleitk_if_absent(monkeypatch):
    """A run that never touches a vox1 primitive should not pay SimpleITK's
    import cost just because a cached ITK-thread value exists."""
    monkeypatch.delitem(sys.modules, "SimpleITK", raising=False)
    apply_itk_threads(3)  # must not raise, must not import SimpleITK
    assert "SimpleITK" not in sys.modules


@pytest.mark.unit
def test_setter_exception_is_reported_not_raised(monkeypatch, capsys):
    class _ProcessObject:
        @staticmethod
        def SetGlobalDefaultNumberOfThreads(n: int) -> None:
            raise RuntimeError("older SimpleITK, no such setter")

    class _FakeSimpleITK:
        ProcessObject = _ProcessObject

    monkeypatch.setitem(sys.modules, "SimpleITK", _FakeSimpleITK)
    apply_itk_threads(4)  # must not raise
    assert "WARNING" in capsys.readouterr().err
