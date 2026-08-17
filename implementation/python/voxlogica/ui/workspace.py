"""The workspace: the one place a document and a view actually live.

Server-side on purpose. An MCP client is not a browser, and "an agent can see and
do what the user sees and does" is only true by construction if the state is not
in a tab: with no tab open there would be nothing to look at, and with two tabs
open there would be two answers. The browser holds a replica of what is here.

Every change goes through `apply`, which is the whole mutation surface: one name,
one place, one broadcast. See doc/dev/ui-workspace.md section 3.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from .actions import ACTIONS
from . import library
from .document import Document, parse

logger = logging.getLogger(__name__)


class Workspace:
    def __init__(self, *, hub=None, path: str | Path | None = None, results=None,
                 compute=None) -> None:
        self._hub = hub
        #: Where node states live. The workspace tells it which names the
        #: document compiled to; it tells the workspace nothing back. Optional
        #: so a workspace can be constructed in a test without one.
        self.results = results
        #: Where a demand goes when a card asks to be computed. Optional for the
        #: same reason `results` is: a workspace in a test runs nothing.
        self.compute = compute
        self._lock = threading.RLock()
        self.path: Path | None = Path(path) if path else None
        text = self.path.read_text() if self.path and self.path.exists() else ""
        self.document: Document = parse(text)
        #: Not part of the document: which page you are on is not a property of
        #: the program, and a diff that changes because someone scrolled is a
        #: diff nobody wants to review.
        self.view: dict[str, Any] = {
            "page": 0, "zoom": 1.0, "selection": [], "focus": None,
            #: `both` by default: the middle of the movement, where a card shows
            #: its program and what that program currently comes to.
            "lens": "both",
            #: Board or document: the two distances Tab swaps between.
            "showing": "board",
        }
        #: Undo is a stack of whole documents, as text.
        #:
        #: The text *is* the document -- export is concatenation and parsing is
        #: lossless -- so a snapshot cannot drift from what it claims to
        #: represent, which an inverse-operation log can and eventually does.
        #: A board is small; a hundred of them is nothing.
        self._past: list[str] = []
        self._future: list[str] = []
        #: Debounced autosave. A workspace is not a thing you save, any more
        #: than a drawer is: the file on disk is the document, and an unsaved
        #: change is only a change nobody has written down yet. Debounced
        #: because a drag is dozens of changes and the file is one.
        self._save_timer: threading.Timer | None = None
        # Nothing is created here. A file exists because somebody asked for one,
        # and an application that wrote a document on every launch left a week
        # of empty files behind for a week of launches.
        if self.path is not None:
            from . import home

            home.remember(self.path)

    # ------------------------------------------------------------------ state

    def follow(self, before: str | Path, after: Path) -> bool:
        """The open file moved or was renamed underneath us; keep looking at it.

        The library moves files as files; the workspace is whichever one is
        open. If that is the one that moved, the open document follows it -- an
        editor pointing at a path that no longer exists would write the next
        change to a file nobody can find.
        """
        with self._lock:
            if self.path is None or Path(before) != self.path:
                return False
            self.path = Path(after)
        self.publish()
        return True

    def follow_folder(self, before: Path, after: Path) -> bool:
        """The same, for a project folder that was renamed."""
        with self._lock:
            path = self.path
            if path is None or before not in path.parents:
                return False
            self.path = after / path.relative_to(before)
        self.publish()
        return True

    def forget(self, path: str | Path) -> bool:
        """A file was deleted. If it was the open one, open nothing."""
        with self._lock:
            if self.path is not None and Path(path) == self.path:
                self.path = None
                self.document = parse("")
                self._past.clear()
                self._future.clear()
        self.publish()
        return True

    def snapshot(self) -> dict[str, Any]:
        """Everything the user sees, which is exactly what MCP reads."""
        with self._lock:
            return {
                "path": str(self.path) if self.path else None,
                #: What to call this workspace: the folder it lives in. The path
                #: is for the file manager, the name is for the person.
                "name": self.path.parent.name if self.path else None,
                "board": self.document.board,
                "cards": self.document.cards,
                "view": dict(self.view),
                "dirty": self.document.dirty,
                #: The file exactly as it would be written, so a client can show
                #: the document itself without a second round trip.
                "source": (source := self.document.to_imgql()),
                #: The library is the tab bar: every file there is, and which
                #: one is open. Read from the filesystem each time, because the
                #: filesystem is the only description of it.
                "library": library.tree(self.path),
                #: What is wrong with the program as it stands, if anything.
                #: Facts rather than errors: somebody halfway through an edit
                #: has a duplicate name for a few seconds and does not need a
                #: dialogue about it, but does want to be told which two cards.
                "issues": self._issues(),
                #: Which node each `let` in this document compiled to. A result
                #: card names a binding -- prose the user typed -- and only the
                #: reducer can say which hash that is. It travels with the
                #: snapshot because it is a property of the text, and the text
                #: is what just changed.
                "nodes": self._nodes(source),
                "canUndo": bool(self._past),
                "canRedo": bool(self._future),
            }

    def _nodes(self, source: str) -> dict[str, str]:
        from .results import bindings_for

        bindings = bindings_for(source)
        if self.results is not None:
            self.results.set_bindings(bindings)
        return bindings

    def _issues(self) -> dict[str, Any]:
        from . import analysis

        cards = self.document.cards
        try:
            analysis.dependency_order(cards)
            cycle: list[str] = []
        except analysis.Cycle as found:
            cycle = list(found.ids)
        return {
            #: Why the document does not compile, if it does not -- a parse
            #: error or a reduction one. First, because nothing else in here
            #: means much until it does.
            "compile": analysis.compile_error(self.document.to_imgql()),
            "cycle": cycle,
            "duplicates": analysis.duplicates(cards),
            #: Cards sharing a cell. The board refuses placements that would
            #: cause this, so a document carrying one arrived that way -- and
            #: saying so is better than a board whose gestures have quietly
            #: stopped making sense.
            "overlaps": [list(pair) for pair in analysis.overlapping(cards)],
        }

    # ---------------------------------------------------------------- actions

    def apply(self, name: str, params: dict[str, Any] | None = None) -> Any:
        action = ACTIONS.get(name)
        if action is None:
            raise KeyError(f"no such action: {name}")
        params = params or {}
        missing = [key for key in action.required if key not in params]
        if missing:
            raise ValueError(f"{name} needs {', '.join(missing)}")
        with self._lock:
            if action.mutates:
                self._past.append(self.document.to_imgql())
                del self._past[:-100]
                # A new edit is a new branch: whatever was undone is not coming
                # back, and offering to redo it would restore a document that
                # never existed.
                self._future.clear()
            result = action.apply(self, params)
        if action.mutates:
            self._autosave()
        self.publish()
        return result

    # ------------------------------------------------------------ persistence

    def _autosave(self, delay: float = 0.4) -> None:
        """Write the document soon, and once, however many changes arrive.

        Every mutation restarts the clock, so a drag that reports twenty
        positions costs one write. There is no save action to forget: the last
        thing that happened is what is on disk.
        """
        if self.path is None:
            return
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(delay, self._write_now)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _write_now(self) -> None:
        try:
            with self._lock:
                # Nothing to write is not the same as writing nothing: touching
                # the file when only the view changed would show up as work in
                # every tool that watches it.
                if self.path is None or not self.document.dirty:
                    return
                # The directory may not exist yet: a scratch workspace names its
                # file before there is anything to put in it, and this is the
                # moment there is.
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Written in an order the language accepts. The board arranges
                # cards in space and space says nothing about reading order, so
                # the order is derived from what each card defines and needs --
                # see analysis.py. A document that cannot be ordered (two cards
                # that need each other) is written as it stands and reported:
                # refusing to save somebody's work over a cycle they are in the
                # middle of resolving would be the worst possible moment to be
                # strict.
                try:
                    self.document.tidy()
                except Exception:  # noqa: BLE001 - a cycle, or source we cannot read
                    logger.debug("could not order this document; writing as it is",
                                 exc_info=True)
                text = self.document.to_imgql()
                self.path.write_text(text)
                # The written text *becomes* the document's own text. Without
                # this, a document that was annotated on its way to disk stays
                # "clean" while remembering the empty file it was read from --
                # and the next export, undo or write hands back that emptiness.
                self.document.source = text
                self.document.dirty = False
        except OSError:
            # A read-only file or a vanished directory is not a reason to take
            # the UI down: the document is still here and still correct.
            return
        self.publish()

    def flush(self) -> None:
        """Write anything pending right now -- on the way out, or before a read."""
        with self._lock:
            timer, self._save_timer = self._save_timer, None
        if timer is not None:
            timer.cancel()
        self._write_now()

    def undo(self) -> bool:
        with self._lock:
            if not self._past:
                return False
            self._future.append(self.document.to_imgql())
            self.document = parse(self._past.pop())
            self.document.dirty = True
        self._autosave()
        return True

    def redo(self) -> bool:
        with self._lock:
            if not self._future:
                return False
            self._past.append(self.document.to_imgql())
            self.document = parse(self._future.pop())
            self.document.dirty = True
        self._autosave()
        return True

    def open(self, path: str) -> bool:
        """Open a document, and remember it as the one to reopen next time."""
        # Whatever the file being left still owed to disk, it is owed now: an
        # unwritten change must not survive as a change to a different file.
        self.flush()
        target = Path(path)
        text = target.read_text() if target.exists() else ""
        with self._lock:
            self.path = target
            self.document = parse(text)
            # A different document, so nothing before it is anything to go back
            # to: undoing into the previous file would be a trap.
            self._past.clear()
            self._future.clear()
        from . import home

        home.remember(target)
        return True

    def set_text(self, text: str) -> bool:
        with self._lock:
            self.document = parse(text)
            self.document.dirty = True
        return True

    def move_to(self, path: str) -> str:
        """Give this workspace a home, and take the old one away.

        The way a scratch becomes a tracked file: one .imgql moved into a
        repository, layout and all, because the layout is in its own comments.
        The previous file is removed once the new one is written -- a workspace
        that existed in two places would be two workspaces by tomorrow.
        """
        from . import home

        target = Path(path).expanduser()
        if target.is_dir() or not target.suffix:
            # A folder was chosen: the document keeps its name inside it.
            target = target / home.DOCUMENT
        with self._lock:
            previous = self.path
            target.parent.mkdir(parents=True, exist_ok=True)
            text = self.document.to_imgql()
            target.write_text(text)
            self.document.source = text
            self.path = target
            self.document.dirty = False
            if previous is not None and previous != target and previous.exists():
                self._retire(previous, target.parent)
        self.publish()
        return str(target)

    @staticmethod
    def _retire(previous: Path, destination: Path) -> None:
        """Take the old copy away, and bring whatever lived beside it along.

        A scratch workspace is a folder so that an image it loads or an output
        it wrote can sit next to the program. Moving the workspace and leaving
        those behind would move the half of it nobody looks at.
        """
        from . import home

        try:
            # Only a folder that existed for this one file follows it out. A
            # project holding several files is a project: moving one of them
            # must not empty it.
            folder = previous.parent
            dedicated = (
                folder != home.workspaces()
                and home.workspaces() in folder.parents
                and len(list(folder.glob(f"*{home.SUFFIX}"))) == 1
            )
            if dedicated:
                for item in previous.parent.iterdir():
                    if item == previous:
                        continue
                    goal = destination / item.name
                    if not goal.exists():
                        shutil.move(str(item), str(goal))
            previous.unlink()
            if dedicated:
                folder.rmdir()
        except OSError:
            # Somebody else's file, a read-only directory, a folder that is not
            # empty for a reason we cannot see. The workspace has moved either
            # way; leaving something behind is not a reason to fail the move.
            logger.debug("could not fully retire %s", previous, exc_info=True)

    def rename(self, name: str) -> str:
        """Rename the workspace's folder. The name is the only handle anyone needs.

        A path is not an identity anybody wants to read: what a person calls
        this piece of work is a word, and the folder is where that word lives so
        that the file manager agrees with the UI.
        """
        cleaned = name.strip().strip("/\\")
        with self._lock:
            path = self.path
        if not cleaned or path is None or cleaned == path.parent.name:
            return str(path) if path else ""
        return self.move_to(str(path.parent.parent / cleaned / path.name))

    def choose_location(self) -> None:
        """Ask the system where this should live, and move it there if answered.

        Off the calling thread on purpose: a modal panel owned by another
        process must not be able to freeze the board behind it, and the answer
        may be minutes away. When it arrives the move publishes itself, so the
        UI updates without having waited for anything.
        """
        from . import native

        suggested = self.path or Path.home() / "workspace.imgql"

        def ask() -> None:
            chosen = native.choose_save_path(suggested)
            if chosen:
                from . import guard

                self.move_to(str(guard.approve(chosen)))

        thread = threading.Thread(target=ask, name="voxlogica-save-dialog", daemon=True)
        thread.start()

    def reveal(self) -> bool:
        """Show this workspace's file in the platform's file manager."""
        from . import native

        with self._lock:
            path = self.path
        if path is None:
            return False
        self.flush()
        return native.reveal(path)

    def save(self, path: str | None = None) -> str:
        with self._lock:
            target = Path(path) if path else self.path
            if target is None:
                raise ValueError("no path to save to")
            target.write_text(self.document.to_imgql())
            self.path = target
            return str(target)

    # -------------------------------------------------------------- broadcast

    def publish(self) -> None:
        """Push the new state to every client, browser and agent alike.

        Sticky, so a tab that connects later gets the workspace as part of
        connecting rather than having to ask for it.
        """
        if self._hub is None:
            return
        self._hub.publish(
            {"type": "workspace", "workspace": self.snapshot()}, sticky_key="workspace"
        )
