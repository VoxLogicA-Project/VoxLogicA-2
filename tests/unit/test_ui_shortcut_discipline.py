"""Nothing is reachable only through a context menu.

A menu is a place to discover a thing; it is a bad place to *do* it twice. The
rule is that every action in a menu also answers to the keyboard, and this test
is what keeps that true as menus grow -- because the day somebody adds an entry
without a chord, nothing visibly breaks and nobody finds out.

Two further rules, both from the same source as the first: every chord takes a
modifier, because the bare letters belong to whoever is typing into a card; and
every chord appears in the help sheet, because a shortcut nobody can find is a
shortcut nobody has.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "implementation" / "ui" / "src"
SHORTCUTS = UI / "lib" / "components" / "Bento" / "shortcuts.js"

#: Menu entries whose keyboard route is a *gesture* rather than a chord, stated
#: in the sheet as such. A second chord for these would be a second name for
#: something the hand already knows.
_EXEMPT = {
    "Rename",  # double-click the name, or F2
    "Maximize",  # double-click
    "Focus",  # long press
    "Leave focus",
}

#: The dev design panel is not the application. It exists to be looked at while
#: building the application, it is not in a production bundle at all, and
#: holding it to the shortcut rule would be inventing chords nobody will press.
_NOT_THE_APP = ("gallery",)

_LABEL = re.compile(r'label:\s*(?:"([^"]+)"|`([^`$]+)`)')


def _sheet() -> str:
    return SHORTCUTS.read_text()


def _menu_labels() -> set[str]:
    labels: set[str] = set()
    for path in UI.rglob("*.svelte"):
        text = path.read_text()
        for quoted, templated in _LABEL.findall(text):
            label = (quoted or templated).strip()
            if label:
                labels.add(label)
    return labels


def test_every_menu_entry_has_a_keyboard_route():
    """The property, checked the only way that survives new menus.

    A label counts as answered when the menu itself states a hint, which is what
    puts the chord under the user's eyes at the moment they are looking for it.
    """
    if not UI.is_dir():
        pytest.skip("no UI sources here (running from a wheel)")

    unanswered = []
    for path in UI.rglob("*.svelte"):
        if any(part in _NOT_THE_APP for part in path.parts):
            continue
        text = path.read_text()
        # An entry is a `label:` and whatever follows it up to the next entry.
        entries = re.split(r"(?=\blabel:)", text)
        for entry in entries[1:]:
            found = _LABEL.match(entry)
            if not found:
                continue
            label = (found.group(1) or found.group(2) or "").strip()
            if not label or label in _EXEMPT:
                continue
            body = entry[: entry.find("},") + 1] or entry[:400]
            # A menu *entry* does something when chosen. A `label:` in a list of
            # card kinds or sort orders is a name for an option, not an action,
            # and has no business having a chord of its own.
            if "onselect:" not in body:
                continue
            if "hint:" in body:
                continue
            unanswered.append(f"{path.name}: {label}")

    assert not unanswered, (
        "these menu entries can only be reached with a pointer. Give each a "
        "chord, register it in shortcuts.js, and put it in the entry's `hint:` "
        "so the menu teaches it:\n  " + "\n  ".join(sorted(unanswered))
    )


def test_no_shortcut_is_a_bare_letter():
    """The letters belong to whoever is typing into a card.

    `f` was focus once; it also became the letter `f` in whatever somebody was
    writing. Every chord takes a modifier, and this is the rule that has no
    exceptions.
    """
    bare = [
        line
        for line in _sheet().splitlines()
        if (found := re.search(r'keys:\s*"([^"]+)"', line))
        and any(
            re.fullmatch(r"[A-Za-z]", part.strip())
            for part in found.group(1).split("/")
        )
    ]
    assert not bare, "a bare letter cannot be a shortcut:\n" + "\n".join(bare)


def test_the_help_sheet_is_not_a_second_list_of_something_else():
    """Every chord the sheet claims is one somebody wrote down deliberately.

    The sheet lives beside the code that implements it for this reason; the test
    only checks it has not become a stub.
    """
    entries = re.findall(r'keys:\s*"([^"]+)"', _sheet())
    assert len(entries) > 20, "the help sheet has lost entries"
    assert len(entries) == len(set(entries)), "the help sheet lists a chord twice"
