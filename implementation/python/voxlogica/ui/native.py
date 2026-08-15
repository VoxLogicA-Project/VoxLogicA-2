"""The two things a browser cannot do about a file on this machine.

Showing a file in the file manager, and asking for a location with the system's
own save panel. Both belong here rather than in the page because the page is a
tab in a sandbox and this process is not: the server is already running on the
user's machine, with their permissions, and it can simply ask the platform.

The dialogue is the platform's, deliberately. A web form pretending to be a file
picker cannot see the filesystem the user is choosing within, and choosing where
a file goes is exactly the moment somebody wants their own sidebar, their own
recent folders and their own iCloud or network mounts.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def reveal(path: Path) -> bool:
    """Show a file in the platform's file manager. False if that is not possible."""
    path = Path(path)
    try:
        if sys.platform == "darwin":
            # -R selects the file in its folder rather than opening the file.
            subprocess.Popen(["open", "-R", str(path)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        if os.name == "nt":
            subprocess.Popen(["explorer", f"/select,{path}"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        opener = shutil.which("xdg-open")
        if opener:
            # No portable "select the file" on Linux; the folder is the answer.
            subprocess.Popen([opener, str(path.parent)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
    except OSError as exc:
        logger.debug("could not reveal %s (%s)", path, exc)
    return False


def _osascript(suggested: Path) -> str | None:
    script = (
        'POSIX path of (choose file name with prompt "Save workspace as"'
        f' default name "{suggested.name}"'
        f' default location POSIX file "{suggested.parent}")'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    # A cancelled panel exits non-zero, which is not an error: it is an answer.
    return result.stdout.strip() or None if result.returncode == 0 else None


def _zenity(suggested: Path) -> str | None:
    tool = shutil.which("zenity")
    if tool:
        result = subprocess.run(
            [tool, "--file-selection", "--save", "--confirm-overwrite",
             f"--filename={suggested}", "--title=Save workspace as"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None
    tool = shutil.which("kdialog")
    if tool:
        result = subprocess.run([tool, "--getsavefilename", str(suggested), "*.imgql"],
                                capture_output=True, text=True)
        return result.stdout.strip() or None if result.returncode == 0 else None
    return None


def _powershell(suggested: Path) -> str | None:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.SaveFileDialog;"
        f"$d.InitialDirectory = '{suggested.parent}';"
        f"$d.FileName = '{suggested.name}';"
        "$d.Filter = 'VoxLogicA workspace (*.imgql)|*.imgql';"
        "if ($d.ShowDialog() -eq 'OK') { $d.FileName }"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True)
    return result.stdout.strip() or None if result.returncode == 0 else None


def choose_save_path(suggested: Path) -> str | None:
    """Ask the system where to put this workspace. `None` if cancelled or absent.

    Blocks until the user answers, so callers run it off whatever thread is
    serving requests -- a modal panel must not be able to freeze the board
    behind it.
    """
    suggested = Path(suggested)
    try:
        if sys.platform == "darwin":
            return _osascript(suggested)
        if os.name == "nt":
            return _powershell(suggested)
        return _zenity(suggested)
    except OSError as exc:
        logger.debug("no system save dialogue available (%s)", exc)
        return None
