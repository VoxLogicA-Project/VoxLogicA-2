"""The manual cannot fall behind the program.

Documentation rots because writing it is a separate act from building the thing,
and the separate act is the one that gets skipped on a busy afternoon. So it is
not a separate act here: an action or a shortcut that `doc/user/manual.md` does
not mention fails the build.

The bar is deliberately low -- *mentioned*, not described well. A test cannot
tell whether a sentence is any good, and pretending otherwise would only teach
people to write a sentence that satisfies a regular expression. What it can tell
is that nobody was told about the feature at all, which is the failure that
actually happens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "doc" / "user" / "manual.md"
ACTIONS = ROOT / "implementation" / "python" / "voxlogica" / "ui" / "actions.py"
SHORTCUTS = (
    ROOT / "implementation" / "ui" / "src" / "lib" / "components" / "Bento" / "shortcuts.js"
)

#: Actions that exist for scripts and agents and have no user-facing gesture at
#: all. Named individually, so adding one is a decision rather than a slip.
_MACHINERY = {
    # The pieces of a drag, which the manual describes as dragging.
    "board.moveCard",
    "board.resizeCard",
    "board.arrange",
    # The clipboard's two halves; the manual documents cut/copy/paste.
    "board.copyCards",
    "board.cutCards",
    "board.pasteCards",
    "library.pasteCards",
    # Bind and mode, reached through the card menu and the lens.
    "card.bindNode",
    "card.setKind",
    "card.setViewMode",
    # Selection and paging, which the manual gives as gestures and arrows.
    "view.select",
    "view.goToPage",
    "view.setZoom",
    "view.focus",
    # Text-level twins of gestures documented as gestures.
    "card.setSource",
    "card.setTitle",
    "board.addCard",
    "board.removeCard",
    "board.duplicateCard",
    "board.deriveCard",
    "board.setPage",
    "library.moveFile",
    "library.copyFile",
    "library.renameFile",
    "library.renameProject",
    "library.deleteFile",
    "library.forgetFolder",
    "workspace.setText",
    "workspace.export",
    "workspace.tidy",
}


def _manual() -> str:
    return MANUAL.read_text()


def test_the_manual_is_there_and_is_short():
    """Usable means readable in one sitting. A manual nobody finishes is a
    manual nobody reads the middle of."""
    text = _manual()
    assert text.strip(), "the manual is empty"
    assert len(text.splitlines()) < 250, "the manual has grown past being read in one sitting"


def test_every_action_a_person_can_reach_is_in_the_manual():
    """The rule, and the reason there is a file to add a line to."""
    names = re.findall(r'_action\(\s*"([^"]+)"', ACTIONS.read_text())
    text = _manual()

    missing = []
    for name in names:
        if name in _MACHINERY:
            continue
        namespace, _, verb = name.partition(".")
        # Mentioned by its own name, or by its MCP name, or by the verb spelled
        # out -- `setLens` as "lens", `addLabel` as "label".
        words = re.findall(r"[a-z]+|[A-Z][a-z]*", verb)
        if name in text or name.replace(".", "_") in text:
            continue
        if all(word.lower().rstrip("s") in text.lower() for word in words):
            continue
        missing.append(name)

    assert not missing, (
        "these can be done and are not written down. Add a line to "
        "doc/user/manual.md:\n  " + "\n  ".join(sorted(missing))
    )


def test_every_shortcut_is_in_the_manual():
    """A chord nobody can find is a chord nobody has."""
    if not SHORTCUTS.exists():
        pytest.skip("no UI sources here (running from a wheel)")
    text = _manual()

    # The manual writes chords the way a Mac keyboard does; the table writes
    # them portably. One is the translation of the other.
    def spoken(keys: str) -> list[str]:
        return [
            part.strip()
            .replace("mod+", "⌘")
            .replace("shift+⌘", "⇧⌘")
            .replace("Backspace", "⌫")
            .replace("shift+arrows", "⇧arrows")
            for part in keys.split("/")
        ]

    missing = []
    for keys in re.findall(r'keys:\s*"([^"]+)"', SHORTCUTS.read_text()):
        if any(form and form in text for form in spoken(keys)):
            continue
        # Gestures are described in prose rather than as keys.
        if all(word in text.lower() for word in keys.lower().split() if len(word) > 3):
            continue
        missing.append(keys)

    assert not missing, (
        "these shortcuts are not in doc/user/manual.md:\n  " + "\n  ".join(sorted(missing))
    )


def test_the_manual_says_how_an_agent_reaches_it():
    """It is the same document for both readers, and an agent that has to guess
    at the vocabulary guesses wrong."""
    text = _manual()
    assert "MCP" in text
    assert "voxlogica_manual" in text, "the manual does not say how to read it over MCP"
