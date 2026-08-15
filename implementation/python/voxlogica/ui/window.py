"""Opening the UI as a window rather than as a tab.

A workspace is an application, not a page: it owns the whole viewport, it has no
use for a URL bar, and a tab lost among thirty others is a workspace you will not
find again. Every Chromium-family browser can already do this -- `--app=URL`
opens a chromeless window using the profile the user is already signed into -- so
this needs no bundled runtime, no packaging step and no second rendering engine
to keep up to date.

If none is installed, the default browser is opened instead. That is a worse
window, not a broken one, and it is the difference between a dependency and a
preference.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

#: Tried in order. The user's default browser is not consulted for this: what is
#: wanted is any engine that can open a chromeless window, and Chromium's flag is
#: the only one that is universally available.
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


def open_window(url: str, *, size: tuple[int, int] = (1280, 860)) -> str:
    """Open `url` as an application window. Returns how it was opened."""
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
