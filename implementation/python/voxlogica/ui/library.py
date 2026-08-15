"""The library: projects, and the files in them.

A project is a folder. A file is an `.imgql` in it. That is the whole model, and
it is deliberately not a model at all -- there is no index, no database and no
manifest, because anything of that kind is a second description of the
filesystem that can disagree with it. Make a folder in Finder and it is a
project; drop a file into it and it is in that project; put the whole thing in a
repository and git has something ordinary to track. Nothing here has to be told.

One file is open at a time and the sidebar is the list of them, so there are no
tabs: a tab bar is a second, worse copy of the list you already have, kept in a
different order, and it is where "which of these nine is the one I mean" comes
from.

Loose files at the top of the library are the default destination -- the place
something goes when nobody has said where. They are as real as any other file;
"unfiled" is a location, not a limbo.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import home

logger = logging.getLogger(__name__)

SUFFIX = ".imgql"

#: Folders that are somebody else's business, not projects.
_HIDDEN = {".git", ".svn", "node_modules", "__pycache__", ".DS_Store"}


def root() -> Path:
    return home.workspaces()


def _safe(name: str) -> str:
    """A file name from something a person typed.

    Only the characters that would make the name mean something else are
    refused: a slash would change which folder this is, a leading dot would hide
    it. Everything else -- spaces, accents, punctuation -- is somebody's
    language and none of our business.
    """
    cleaned = re.sub(r"[/\\\x00]", "-", name).strip().strip(".")
    return cleaned or "untitled"


@dataclass(frozen=True)
class Entry:
    """One file in the library."""

    path: Path
    project: str | None

    def as_dict(self, *, open_path: Path | None = None) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.path.stem,
            "project": self.project,
            "open": open_path is not None and self.path == open_path,
            "modified": self.path.stat().st_mtime if self.path.exists() else 0,
        }


def scan() -> list[Entry]:
    """Every file in the library, loose ones first, each in file-name order."""
    base = root()
    if not base.is_dir():
        return []
    loose = [Entry(path, None) for path in sorted(base.glob(f"*{SUFFIX}")) if path.is_file()]
    filed: list[Entry] = []
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.name in _HIDDEN or folder.name.startswith("."):
            continue
        filed.extend(
            Entry(path, folder.name) for path in sorted(folder.glob(f"*{SUFFIX}")) if path.is_file()
        )
    return loose + filed


def projects() -> list[str]:
    """Every project folder, including ones that hold no files yet.

    An empty project is a real thing: somebody made it because they are about to
    put something in it, and a list that hid it until then would be a list that
    forgets what you just did.
    """
    base = root()
    if not base.is_dir():
        return []
    return sorted(
        folder.name
        for folder in base.iterdir()
        if folder.is_dir() and folder.name not in _HIDDEN and not folder.name.startswith(".")
    )


def tree(open_path: Path | None = None) -> dict[str, Any]:
    """The library as the sidebar reads it."""
    entries = [entry.as_dict(open_path=open_path) for entry in scan()]
    return {
        "root": str(root()),
        "projects": projects(),
        "files": entries,
    }


def new_file(project: str | None = None, name: str | None = None) -> Path:
    """A new, empty file -- in a project, or loose at the top of the library."""
    folder = root() / _safe(project) if project else root()
    folder.mkdir(parents=True, exist_ok=True)
    stem = _safe(name) if name else datetime.now().strftime("%Y-%m-%d-%H%M%S")
    candidate = folder / f"{stem}{SUFFIX}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{n}{SUFFIX}"
        n += 1
    candidate.touch()
    return candidate


def new_project(name: str) -> str:
    folder = root() / _safe(name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder.name


def move(path: str | Path, project: str | None) -> Path:
    """Move a file into a project, or out to the top of the library.

    Assets are not dragged along: within one library a file's neighbours are the
    project's, and a move inside a library is a move between two folders that
    both already exist. Moving *out* of the library is `workspace.moveTo`, which
    is a different act with different consequences.
    """
    source = Path(path)
    destination = (root() / _safe(project) if project else root()) / source.name
    if source == destination:
        return source
    destination.parent.mkdir(parents=True, exist_ok=True)
    n = 2
    while destination.exists():
        destination = destination.with_name(f"{source.stem}-{n}{SUFFIX}")
        n += 1
    shutil.move(str(source), str(destination))
    return destination


def rename(path: str | Path, name: str) -> Path:
    source = Path(path)
    destination = source.with_name(f"{_safe(name)}{SUFFIX}")
    if destination != source:
        if destination.exists():
            raise FileExistsError(f"{destination.name} already exists here")
        source.rename(destination)
    return destination


def rename_project(name: str, to: str) -> str:
    folder = root() / _safe(name)
    destination = root() / _safe(to)
    if folder != destination:
        if destination.exists():
            raise FileExistsError(f"{destination.name} already exists")
        folder.rename(destination)
    return destination.name


def delete(path: str | Path) -> bool:
    """Remove a file. Folders are left alone even when they empty out.

    A project that has just lost its last file is still a project somebody made,
    and removing it because it is momentarily empty would be the library
    deciding it knows better.
    """
    target = Path(path)
    try:
        target.unlink()
        return True
    except OSError:
        logger.debug("could not delete %s", target, exc_info=True)
        return False
