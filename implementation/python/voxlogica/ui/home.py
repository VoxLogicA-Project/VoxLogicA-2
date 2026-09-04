"""Where a workspace lives when nobody has said where it should live.

`voxlogica` with no arguments opens **the document you had open last**, and if
there is none it opens nothing and offers to make one. It used to create a file
named after the minute at every launch, which meant a week of opening the
application left a week of empty documents nobody had asked for.

Nothing here creates a file. `scratch_path` says what a new one would be called;
somebody has to ask for it.

Moving a document into a repository is a later, deliberate act --
`workspace.moveTo` -- and it is cheap because a workspace *is* one .imgql file:
the layout lives in its own comments, so a scratch that turns out to matter
becomes a tracked file by being moved, and diffs like source from then on.
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


#: Where the last document opened is remembered. Not in any document: which
#: file you had open is a fact about a person's session, not about a program,
#: and putting it in one would put it in a diff.
_LAST = "last-opened"


def last_opened() -> Path | None:
    """The document this user was working on, if it is still there.

    Checked rather than trusted: a remembered path that has since been deleted,
    renamed outside the app, or moved to a disk that is not mounted must open
    nothing rather than recreate something.
    """
    record = data_home() / _LAST
    try:
        path = Path(record.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return path if path.is_file() else None


def remember(path: Path | str | None) -> None:
    """Keep the last document opened. Best-effort: an unwritable state
    directory is a worse next launch, not a failure now."""
    record = data_home() / _LAST
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        if path is None:
            record.unlink(missing_ok=True)
        else:
            record.write_text(str(path), encoding="utf-8")
    except OSError:
        pass


def window_state_path() -> Path:
    """Where the native window keeps what a browser profile would keep.

    Beside the workspaces rather than inside them: cookies and local storage are
    something the application accumulated, not something the user wrote, and a
    directory the user might put under git should contain only the latter.
    """
    path = data_home() / "window"
    path.mkdir(parents=True, exist_ok=True)
    return path


#: What a workspace file is called when a *folder* was chosen for it -- the
#: system save panel picks folders as readily as names, and a folder needs a
#: document inside it.
DOCUMENT = "workspace.imgql"

SUFFIX = ".imgql"


def scratch_path(now: datetime | None = None) -> Path:
    """A fresh file, loose at the top of the library.

    The top is the default destination: the place something goes when nobody has
    said where, and where it stays until somebody drags it into a project.
    Projects are folders and a folder is what travels into a repository, so a
    new file does not get one of its own -- an untouched workspace should not
    leave a directory behind for having been opened once.

    Named after when it was started, because the only thing anyone remembers
    about an unnamed workspace is roughly when they were working on it.
    """
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    candidate = workspaces() / f"{stamp}{SUFFIX}"
    n = 2
    while candidate.exists():
        candidate = workspaces() / f"{stamp}-{n}{SUFFIX}"
        n += 1
    return candidate


def recent(limit: int = 20) -> list[Path]:
    """Scratch workspaces, most recently written first."""
    directory = workspaces()
    if not directory.is_dir():
        return []
    files = [path for path in directory.rglob(f"*{SUFFIX}") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit]
