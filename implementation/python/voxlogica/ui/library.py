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

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import home, labels

logger = logging.getLogger(__name__)

SUFFIX = ".imgql"

#: Folders that are somebody else's business, not projects.
_HIDDEN = {".git", ".svn", "node_modules", "__pycache__", ".DS_Store"}


def root() -> Path:
    return home.workspaces()


#: The name the shipped examples appear under. One word, because it is what the
#: folder is, and because a person scanning a sidebar reads the first one.
EXAMPLES = "Examples"


def examples_root() -> Path | None:
    """Where the programs that ship with VoxLogicA are, if they are here.

    Nobody should have to find and mount these by hand: the first question a new
    workspace raises is "what does one of these look like", and the answer is on
    disk already. So they are a project that is simply always there.

    `None` when there are none -- an installed wheel carries the code and not the
    examples, and a project pointing at a folder that does not exist would be a
    row that does nothing when clicked.
    """
    # library.py -> ui -> voxlogica -> python -> implementation -> the checkout.
    # `doc/gallery` is where they actually live: a reading path through the
    # language, ordered in its own README, every program self-contained and
    # printing something. Pointing anywhere else would be inventing a second
    # collection beside the one that is maintained.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "doc" / "gallery" / "programs"
        if candidate.is_dir() and any(candidate.rglob(f"*{SUFFIX}")):
            return candidate
    return None


def is_builtin(folder: Path) -> bool:
    """Whether this project is one of ours rather than one of theirs.

    It is still an ordinary folder of ordinary files -- open them, run them, copy
    a card out of them. What it is not is *theirs to lose*: forgetting a project
    they never added would be an offer to break something they did not build.
    """
    found = examples_root()
    return found is not None and folder.resolve() == found.resolve()


#: Folders elsewhere on disk that are shown as projects too.
#:
#: A list of *locations*, not of content: what is in them is still whatever the
#: filesystem says, read fresh every time. This is the one thing the filesystem
#: cannot tell us on its own -- that somebody wants a repository they already
#: have to appear here -- and it is exactly what a VS Code workspace file is.
def _links_file() -> Path:
    return home.data_home() / "projects.json"


def links() -> list[Path]:
    try:
        raw = json.loads(_links_file().read_text())
    except (OSError, ValueError):
        return []
    seen: list[Path] = []
    for entry in raw if isinstance(raw, list) else []:
        path = Path(str(entry)).expanduser()
        if path.is_dir() and path not in seen:
            seen.append(path)
    return seen


def link(path: str | Path) -> str:
    """Show an existing folder as a project. Nothing is moved or copied.

    A folder that is already a project -- because it lives in the library, or
    because it was linked before -- is not added again. It listed its files
    twice, which is what a second entry pointing at one directory means.
    """
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    if folder.parent == root().resolve():
        # Already a project by virtue of where it is.
        return folder.name
    current = links()
    if folder not in current:
        current.append(folder)
        _links_file().parent.mkdir(parents=True, exist_ok=True)
        _links_file().write_text(json.dumps([str(item) for item in current], indent=2))
    return folder.name


def unlink(path: str | Path) -> bool:
    """Stop showing a linked folder. The folder itself is not touched."""
    folder = Path(path).expanduser().resolve()
    remaining = [item for item in links() if item != folder]
    _links_file().parent.mkdir(parents=True, exist_ok=True)
    _links_file().write_text(json.dumps([str(item) for item in remaining], indent=2))
    return True


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
            #: Read from the file's own head, memoised on its mtime and size.
            #: A label belongs to the file that carries it, so it survives a
            #: `git mv` this UI never saw. See labels.py.
            "labels": labels.of(self.path),
        }


def _folders() -> list[tuple[str, Path]]:
    """(name, folder) for every project: the library's own, then linked ones.

    Deduplicated by where the folder actually is. Two entries pointing at one
    directory -- a link to a folder already in the library, the same folder
    linked twice by different paths -- would list its files twice, which is the
    list disagreeing with the filesystem about how many files there are.
    """
    base = root()
    inside = (
        sorted(
            (folder.name, folder)
            for folder in base.iterdir()
            if folder.is_dir() and folder.name not in _HIDDEN and not folder.name.startswith(".")
        )
        if base.is_dir()
        else []
    )
    seen = {folder.resolve() for _name, folder in inside}
    out = list(inside)
    shipped = examples_root()
    if shipped is not None and shipped.resolve() not in seen:
        seen.add(shipped.resolve())
        out.append((EXAMPLES, shipped))
    for folder in links():
        resolved = folder.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((folder.name, folder))
    return out


def scan() -> list[Entry]:
    """Every file in the library, loose ones first, each in file-name order."""
    base = root()
    loose = (
        [Entry(path, None) for path in sorted(base.glob(f"*{SUFFIX}")) if path.is_file()]
        if base.is_dir()
        else []
    )
    filed: list[Entry] = []
    for name, folder in _folders():
        # A project is a flat folder of programs, and that is the model
        # everywhere except here: the examples ship one folder per example,
        # because an example is a program *and its data*. Looking one level in
        # for those is what makes them appear at all -- and it is confined to
        # the folders we ship, so nobody's own project starts behaving
        # differently from the folder they can see in Finder.
        pattern = f"**/*{SUFFIX}" if is_builtin(folder) else f"*{SUFFIX}"
        filed.extend(
            Entry(path, name) for path in sorted(folder.glob(pattern)) if path.is_file()
        )
    return loose + filed


def projects() -> list[dict[str, Any]]:
    """Every project, including ones that hold no files yet.

    An empty project is a real thing: somebody made it because they are about to
    put something in it, and a list that hid it until then would be a list that
    forgets what you just did.
    """
    linked = {str(folder) for folder in links()}
    return [
        {
            "name": name,
            "path": str(folder),
            "linked": str(folder) in linked,
            #: Ours, and so not theirs to forget, rename or delete.
            "builtin": is_builtin(folder),
        }
        for name, folder in _folders()
    ]


def tree(open_path: Path | None = None) -> dict[str, Any]:
    """The library as the sidebar reads it."""
    entries = [entry.as_dict(open_path=open_path) for entry in scan()]
    return {
        "root": str(root()),
        "projects": projects(),
        "files": entries,
    }


def folder_of(project: str | None) -> Path:
    """Where a project's files are: inside the library, or wherever it was linked."""
    if project is None:
        return root()
    for name, folder in _folders():
        if name == project:
            return folder
    return root() / _safe(project)


class ReadOnlyProject(Exception):
    """Somebody tried to write into a project that ships with the program."""


def _writable(folder: Path) -> Path:
    """The folder, if it is somebody's to write in.

    The examples are there to be read, run and copied out of -- not to be the
    accidental destination of a new file because they happened to be the
    selected project. Refusing says so; quietly putting the file somewhere else
    would leave them looking for it.
    """
    if is_builtin(folder):
        raise ReadOnlyProject(f"{EXAMPLES} ships with VoxLogicA; copy a file out of it instead")
    return folder


def new_file(project: str | None = None, name: str | None = None) -> Path:
    """A new, empty file -- in a project, or loose at the top of the library."""
    folder = _writable(folder_of(project))
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
    destination = folder_of(project) / source.name
    if source == destination:
        return source
    destination.parent.mkdir(parents=True, exist_ok=True)
    n = 2
    while destination.exists():
        destination = destination.with_name(f"{source.stem}-{n}{SUFFIX}")
        n += 1
    shutil.move(str(source), str(destination))
    return destination


def copy(path: str | Path, project: str | None, name: str | None = None) -> Path:
    """Copy a file into a project, or to the top of the library.

    The copy is a copy of the *file*, contents and all: a workspace is one
    .imgql, so duplicating a piece of work is duplicating one file, and what
    comes back is something you can rename and change without touching the
    original.
    """
    source = Path(path)
    folder = folder_of(project)
    folder.mkdir(parents=True, exist_ok=True)
    stem = _safe(name) if name else source.stem
    destination = folder / f"{stem}{SUFFIX}"
    n = 2
    while destination.exists():
        destination = folder / f"{stem}-{n}{SUFFIX}"
        n += 1
    shutil.copy2(source, destination)
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


def delete_project(name: str) -> bool:
    """Remove an empty project folder.

    Empty only. Deleting a project that still holds files would be deleting the
    files, which is a different and much heavier act than tidying away a folder
    somebody made and did not use -- and one the sidebar can already do
    explicitly, file by file, with a confirmation.

    A linked folder is not ours to delete: forgetting it is `unlink`.
    """
    folder = root() / _safe(name)
    if folder.resolve() in {item.resolve() for item in links()}:
        return False
    if not folder.is_dir():
        return False
    # macOS leaves these behind in any folder a Finder window has looked at, and
    # a folder that is empty apart from one is empty.
    for junk in folder.glob(".DS_Store"):
        junk.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        # Not empty, or not ours to remove. Either way nothing was lost.
        return False
    return True


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
