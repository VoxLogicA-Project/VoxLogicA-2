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


def test_the_inspector_stays_shut_unless_it_is_asked_for(monkeypatch):
    """It was tied to "is this a source checkout", which meant everybody working
    on VoxLogicA got a devtools pane in their face at every startup -- the
    inspector deciding when it was wanted rather than the person."""
    started: list[dict] = []

    class FakeWebview:
        @staticmethod
        def create_window(*_args, **_kwargs):
            class W:
                events = type("E", (), {"closed": []})()

            return W()

        @staticmethod
        def start(**kwargs):
            started.append(kwargs)

    monkeypatch.setitem(sys.modules, "webview", FakeWebview)

    monkeypatch.delenv("VOXLOGICA_DEVTOOLS", raising=False)
    window.run_native("http://127.0.0.1:10001/")
    assert started[-1]["debug"] is False

    monkeypatch.setenv("VOXLOGICA_DEVTOOLS", "1")
    window.run_native("http://127.0.0.1:10001/")
    assert started[-1]["debug"] is True


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

    monkeypatch.setattr(window, "_display_available", lambda: True)
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

    def took_it(url):
        opened.append(url)
        return True

    monkeypatch.setattr(window, "_display_available", lambda: True)
    monkeypatch.setattr(window, "_candidates", lambda: [])
    monkeypatch.setattr(window.webbrowser, "open", took_it)

    assert window.open_window("http://127.0.0.1:10001/") == "browser"
    assert opened == ["http://127.0.0.1:10001/"]


def test_nothing_is_launched_when_there_is_no_display(monkeypatch):
    """The bug this exists for: on a headless machine `Popen` succeeds, the
    browser exits a moment later having found no display, and the process waits
    out its patience for a window that was never on screen -- so the workspace
    looks like it failed to start when in fact it started and said where it was.
    """
    monkeypatch.setattr(window, "_display_available", lambda: False)
    monkeypatch.setattr(
        window, "_candidates", lambda: pytest.fail("looked for a browser anyway")
    )
    monkeypatch.setattr(
        window.webbrowser, "open", lambda url: pytest.fail("opened a tab anyway")
    )

    assert window.open_window("http://127.0.0.1:10001/") == "none"


def test_a_browser_that_declined_is_not_reported_as_a_window(monkeypatch):
    """`webbrowser.open` returns False when it found nothing to open with, and
    a caller told "browser" then waits for a client that is not coming."""
    monkeypatch.setattr(window, "_display_available", lambda: True)
    monkeypatch.setattr(window, "_candidates", lambda: [])
    monkeypatch.setattr(window.webbrowser, "open", lambda url: False)

    assert window.open_window("http://127.0.0.1:10001/") == "none"


def test_the_native_probe_is_not_even_attempted_without_a_display(monkeypatch):
    """Asking pywebview costs two tracebacks at ERROR per missing backend, for
    a question whose answer is already known once there is no display."""
    monkeypatch.delenv(window._DISABLE, raising=False)
    monkeypatch.setattr(window, "_display_available", lambda: False)
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            pytest.fail("imported pywebview with no display")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert window.native_available() is False


def test_a_display_is_what_makes_a_window_possible(monkeypatch):
    """The Unix rule, stated once so a refactor cannot quietly invert it."""
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        pytest.skip("no DISPLAY on this platform")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert window._display_available() is False
    monkeypatch.setenv("DISPLAY", ":0")
    assert window._display_available() is True


def test_window_state_lives_outside_the_workspaces(tmp_path, monkeypatch):
    """What the window accumulates must not land in a directory under git."""
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path))
    state = home.window_state_path()
    assert state.is_dir()
    assert home.workspaces() not in state.parents
    assert state != home.workspaces()
