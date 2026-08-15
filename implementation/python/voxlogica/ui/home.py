"""Where a workspace lives when nobody has said where it should live.

Starting work must not begin with a decision. `voxlogica` with no arguments opens
a workspace that already has a file behind it, in the place this platform keeps
application data, named after the moment it was started. Nothing is asked, and
nothing is lost: autosave has somewhere to write from the first keystroke.

Moving it into a repository is a later, deliberate act -- `workspace.moveTo` --
and it is cheap because a workspace *is* one .imgql file: the layout lives in its
own comments, so a scratch that turns out to matter becomes a tracked file by
being moved, and diffs like source from then on.

The file is not created until something changes. An opened-and-abandoned session
leaves nothing behind.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path


def data_home_parts(
    platform: str, os_name: str, environ: dict[str, str], home: str
) -> tuple[str, ...]:
    """The path, as segments, for a platform that may not be this one.

    Split out from `data_home` so portability can be *tested* rather than
    asserted: constructing a Windows path on a Mac raises, so the decision has
    to be expressible without building a path to make it.
    """
    override = environ.get("VOXLOGICA_HOME")
    if override:
        return (override,)
    if platform == "darwin":
        return (home, "Library", "Application Support", "VoxLogicA")
    if os_name == "nt":
        base = environ.get("LOCALAPPDATA")
        return (base, "VoxLogicA") if base else (home, "AppData", "Local", "VoxLogicA")
    base = environ.get("XDG_DATA_HOME")
    return (base, "voxlogica") if base else (home, ".local", "share", "voxlogica")


def data_home() -> Path:
    """The platform's own place for application data.

    Not a dotfile in `$HOME`: on every platform there is an answer to this
    question already, and inventing a different one means the user's backup and
    sync tools do not know about ours.
    """
    parts = data_home_parts(sys.platform, os.name, dict(os.environ), str(Path.home()))
    return Path(*parts).expanduser()


def workspaces() -> Path:
    return data_home() / "workspaces"


#: The document inside a workspace folder. One fixed name, so a folder is
#: recognisably a workspace and a tool that finds one knows what to open.
DOCUMENT = "workspace.imgql"


def scratch_path(now: datetime | None = None) -> Path:
    """A fresh workspace: its own folder, with the document inside it.

    A folder rather than a bare file, because a workspace accumulates things
    that belong beside the program -- an image it loads, an output it wrote, a
    figure someone dropped in. Those want to travel with it, and they can only
    travel with it if there is an "it" to travel: moving the folder into a
    repository moves the whole piece of work.

    Named after when it was started, because the only thing anyone remembers
    about an unnamed workspace is roughly when they were working on it.
    """
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    directory = workspaces() / stamp
    n = 2
    while directory.exists():
        directory = workspaces() / f"{stamp}-{n}"
        n += 1
    return directory / DOCUMENT


def recent(limit: int = 20) -> list[Path]:
    """Scratch workspaces, most recently written first."""
    directory = workspaces()
    if not directory.is_dir():
        return []
    files = [folder / DOCUMENT for folder in directory.iterdir() if folder.is_dir()]
    files = [path for path in files if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit]
