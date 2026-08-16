"""The application window: the parts where a mistake is invisible in a window.

What is tested here is not "a window appeared" -- that needs a screen, and a
screen is not available in CI. It is the three decisions around the window that
are silent when they go wrong: that asking for the native backend does not cost
the engine its parallelism, that ruling it out falls back rather than raises,
and that the fallback ladder is ordered the way the module claims.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from voxlogica.ui import home, window


def test_native_backend_does_not_re_enable_the_gil():
    """The constraint that rules out `watchdog` also applies here.

    A C extension that has not declared free-threaded support turns the GIL back
    on for the whole process, and an interpreter that quietly stopped being
    free-threaded is the kind of regression that shows up months later as "the
    engine got slower". PyObjC >= 12 declares it; this fails the day a
    dependency bump stops declaring it.
    """
    if not hasattr(sys, "_is_gil_enabled"):
        pytest.skip("not a free-threaded interpreter")
    pytest.importorskip("webview")

    # In a fresh interpreter, because this one has already imported SimpleITK,
    # which turns the GIL on by itself. Asking the question in a process that
    # has answered it another way tells you nothing about the web view.
    probe = (
        "import sys, webview, webview.guilib;"
        "print(sys._is_gil_enabled())"
    )
    result = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", (
        "importing the web view backend re-enabled the GIL; see window.py\n"
        f"{result.stderr}"
    )


def test_native_can_be_switched_off(monkeypatch):
    """The escape hatch is checked before anything is imported."""
    monkeypatch.setenv("VOXLOGICA_NO_NATIVE_WINDOW", "1")
    assert window.native_available() is False


def test_native_availability_never_raises(monkeypatch):
    """A missing runtime is an answer, not an exception.

    `native_available` is asked while there is still somewhere to fall back to,
    so every way it can fail has to arrive as `False`.
    """
    monkeypatch.delenv("VOXLOGICA_NO_NATIVE_WINDOW", raising=False)

    def boom(*_args, **_kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(window, "_DISABLE", "VOXLOGICA_NO_NATIVE_WINDOW")
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            boom()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert window.native_available() is False


def test_fallback_prefers_a_chromeless_window_over_a_tab(monkeypatch):
    """`--app` before `webbrowser`, and the URL is passed as one argument.

    Splitting `--app=URL` into two arguments opens a normal window at that URL,
    which looks close enough to right that nobody notices it is wrong.
    """
    launched: list[list[str]] = []

    monkeypatch.setattr(window, "_candidates", lambda: ["/bin/echo"])
    monkeypatch.setattr(
        window.subprocess, "Popen", lambda argv, **kw: launched.append(argv)
    )
    monkeypatch.setattr(window.webbrowser, "open", lambda url: pytest.fail("used a tab"))

    assert window.open_window("http://127.0.0.1:10001/") == "window"
    assert launched[0][1] == "--app=http://127.0.0.1:10001/"


def test_fallback_of_the_fallback_is_a_tab(monkeypatch):
    """With nothing installed, a tab is a worse window rather than no window."""
    opened: list[str] = []
    monkeypatch.setattr(window, "_candidates", lambda: [])
    monkeypatch.setattr(window.webbrowser, "open", lambda url: opened.append(url))

    assert window.open_window("http://127.0.0.1:10001/") == "browser"
    assert opened == ["http://127.0.0.1:10001/"]


def test_window_state_lives_outside_the_workspaces(tmp_path, monkeypatch):
    """What the window accumulates must not land in a directory under git."""
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path))
    state = home.window_state_path()
    assert state.is_dir()
    assert home.workspaces() not in state.parents
    assert state != home.workspaces()
