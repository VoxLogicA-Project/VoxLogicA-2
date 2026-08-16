"""Opening the UI as a window rather than as a tab.

A workspace is an application, not a page: it owns the whole viewport, it has no
use for a URL bar, and a tab lost among thirty others is a workspace you will not
find again.

There are two ways to get that window, and they are not equals.

**The window this wants** is the operating system's own web view -- WKWebView on
macOS, WebView2 on Windows, WebKitGTK on Linux -- driven by `pywebview`. It is a
real application window: it has a dock icon and a menu bar, ⌘Q means what it
always means, and closing it *is* the application ending rather than a signal
inferred from a heartbeat that stopped arriving. No rendering engine is bundled,
nothing is packaged, and the ~100 MB of Chromium an Electron-shaped answer would
carry is 100 MB the user already has.

**The window it settles for** is a Chromium-family browser opened with `--app`,
and after that the default browser. Both are worse windows rather than broken
ones, and they are what makes the native path a preference instead of a
dependency: a machine with no web view runtime still gets a workspace.

Two constraints shape the code below and neither is negotiable:

* **The native window must own the main thread.** Cocoa and GTK both insist on
  it. `run_native` therefore *blocks* -- it is the last thing the process does --
  and the HTTP server it points at is already on a thread of its own. This is
  why `voxlogica run` does not get a native window: its main thread is busy
  computing, which is the whole reason anyone opened the UI.
* **It must not cost the engine its parallelism.** Importing a C extension that
  has not declared free-threaded support re-enables the GIL for the entire
  process, which is why `watcher.py` refuses `watchdog`. PyObjC ≥ 12 declares
  it, and this is asserted rather than assumed: `tests/unit/test_ui_window.py`
  imports the backend and fails if `sys._is_gil_enabled()` comes back true.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

#: Tried in order, and only once the native web view has been ruled out. The
#: user's default browser is not consulted for this: what is wanted is any engine
#: that can open a chromeless window, and Chromium's flag is the only one that is
#: universally available.
_MACOS_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
)

_COMMANDS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
    "vivaldi",
)

#: Set to any non-empty value to skip the native path. For testing the fallback,
#: and for the user on the far end of a `ssh -X` who wants the browser they have.
_DISABLE = "VOXLOGICA_NO_NATIVE_WINDOW"


def _candidates() -> list[str]:
    found: list[str] = []
    if sys.platform == "darwin":
        found.extend(path for path in _MACOS_APPS if Path(path).is_file())
    for command in _COMMANDS:
        path = shutil.which(command)
        if path:
            found.append(path)
    if os.name == "nt":
        for base in filter(None, (os.environ.get("PROGRAMFILES"),
                                  os.environ.get("PROGRAMFILES(X86)"),
                                  os.environ.get("LOCALAPPDATA"))):
            candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                found.append(str(candidate))
    return found


def native_available() -> bool:
    """Whether a system web view can be driven here.

    Importing the package is not enough to know: `pywebview` installs happily on
    a machine with no GUI runtime and only fails when asked for a window. So the
    backend itself is resolved, which is the same question the window will ask a
    moment later, asked while there is still somewhere to fall back to.
    """
    if os.environ.get(_DISABLE):
        return False
    try:
        import importlib

        # `import webview` first: the package sets its own `guilib` attribute to
        # `None` until a backend is chosen, so `from webview import guilib`
        # hands back that placeholder rather than the module. Importing by name
        # is the difference between resolving the backend and appearing to.
        importlib.import_module("webview")
        guilib = importlib.import_module("webview.guilib")
        backend = guilib.initialize()
    except Exception as exc:  # ImportError, or a runtime with no backend
        logger.debug("no native web view available (%s)", exc)
        return False
    logger.debug("native web view backend: %s", getattr(backend, "__name__", backend))
    return True


def run_native(
    url: str,
    *,
    title: str = "VoxLogicA",
    size: tuple[int, int] = (1280, 860),
    minimum: tuple[int, int] = (720, 480),
    devtools: bool = False,
    storage: Path | None = None,
    on_closed: Callable[[], None] | None = None,
) -> None:
    """Show `url` in the system web view and block until the window closes.

    **Main thread only.** Returns when the user closes the window, which is the
    application's own definition of being over -- no patience constant, no
    inferring an absence from a heartbeat that stopped.
    """
    import webview

    window = webview.create_window(
        title,
        url,
        width=size[0],
        height=size[1],
        min_size=minimum,
        # A workspace is text one edits; a window that would not let you select
        # any of it reads as a screenshot of an application.
        text_select=True,
        confirm_close=False,
    )
    if on_closed is not None:
        window.events.closed += on_closed

    # `private_mode=False` because the workspace keeps things in the browser it
    # is entitled to keep -- and a window that forgot which page you were on
    # every time you opened it would be a window nobody would use twice.
    webview.start(
        debug=devtools,
        private_mode=False,
        storage_path=str(storage) if storage else None,
    )


def open_window(url: str, *, size: tuple[int, int] = (1280, 860)) -> str:
    """Open `url` as a detached application window. Returns how it was opened.

    The fallback path, and unlike :func:`run_native` it does not block: the
    window belongs to another process, so its lifetime is observed through the
    hub rather than awaited here.
    """
    width, height = size
    for executable in _candidates():
        try:
            subprocess.Popen(
                [executable, f"--app={url}", f"--window-size={width},{height}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Detached: closing the workspace window must not depend on this
                # process, and this process must not wait on the browser.
                start_new_session=True,
            )
            return "window"
        except OSError as exc:  # noqa: PERF203 - one failure should not stop the search
            logger.debug("could not launch %s (%s)", executable, exc)
    webbrowser.open(url)
    return "browser"
