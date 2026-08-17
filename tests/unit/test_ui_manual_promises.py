"""The manual is the requirements document, so its promises are tests.

Most of what it says has been checked in pieces, each beside the code that
implements it. This file checks the promises *as promises*: the sentences a
person reads before they use the application, in the order they meet them.

What is here is what can be asserted without a browser. The gestures --
dragging, the chords, what a canvas paints -- are checked by driving a real
instance (see the sweep in the session that added this file); what remains
checkable here is every claim about behaviour that does not need pixels, and
those are the ones that rot silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.ui import analysis, home
from voxlogica.ui.document import parse
from voxlogica.ui.workspace import Workspace

MANUAL = Path(__file__).resolve().parents[2] / "doc" / "user" / "manual.md"

PROGRAM = 'let a = 2\nlet b = 3\nlet s = a + b\nprint "total" s\nsave "out.json" s\n'


@pytest.fixture()
def home_of(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path / "home"))
    return tmp_path


# ------------------------------------------------------------------ starting


def test_starting_up_creates_nothing(home_of):
    """"No file is ever created that you did not ask for." Checked by the
    absence of the directory as well: a home that springs into existence is
    already half a file."""
    assert home.last_opened() is None
    assert not (home_of / "home").exists()


def test_the_document_opened_last_is_the_one_reopened(home_of):
    document = home_of / "kept.imgql"
    document.write_text(PROGRAM)
    Workspace(path=None).open(str(document))
    assert home.last_opened() == document


# --------------------------------------------------- the file and the board


def test_opening_a_file_never_modifies_it(tmp_path):
    """The promise with the most to lose."""
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    space = Workspace(path=path)
    _ = space.snapshot()
    space.flush()
    assert path.read_text() == PROGRAM


def test_print_and_save_each_become_a_card():
    assert [card["kind"] for card in parse(PROGRAM).cards] == ["code", "print", "save"]


def test_there_is_no_save_action_to_remember(tmp_path):
    """"There is no Save": a change is written shortly after it stops
    arriving, so flushing is what a shutdown does rather than what a user
    does."""
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    space = Workspace(path=path)
    space.apply("card.setTitle", {"id": "program", "title": "Renamed"})
    space.flush()
    assert 'title="Renamed"' in path.read_text()


# ------------------------------------------------------------------ running


def test_a_card_can_ask_for_a_binding_no_goal_names():
    """"Run computes what that card is about" -- including names the program
    itself never prints."""
    from voxlogica.ui.compute import _execute_with_engine
    from voxlogica.ui.hub import Hub
    from voxlogica.ui.results import Results, bindings_for

    results = Results(Hub())
    node = bindings_for(PROGRAM)["a"]
    _execute_with_engine(PROGRAM, [node], results.observe)
    assert results.state_of(node)["state"] == "done"


def test_a_selection_is_answered_in_the_document_s_context():
    """"Select any part of a program and the footer says whether this machine
    has already worked it out.\""""
    from voxlogica.ui.results import bindings_for, hash_of

    assert hash_of(PROGRAM, "a + b") == bindings_for(PROGRAM)["s"]


def test_a_program_that_does_not_compile_says_why():
    assert analysis.compile_error("let mask = threshold(flair, 0.6)\n") is not None
    assert analysis.compile_error(PROGRAM) is None


# ------------------------------------------------------------------- labels


def test_a_label_travels_in_the_file(tmp_path):
    """"Labels are written into the file" -- which is the whole reason they
    survive a git mv."""
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    space = Workspace(path=path)
    space.apply("library.addLabel", {"path": str(path), "label": "draft"})
    space.flush()  # the write is debounced, like every other change
    assert 'labels="draft"' in path.read_text()


# -------------------------------------------------------------------- cards


def test_cards_never_share_a_cell():
    document = parse(
        "//@board cols=12 rows=8\n"
        "//@card id=a kind=code x=0 y=0 w=4 h=3\nlet a = 1\n"
        "//@card id=b kind=code x=5 y=0 w=4 h=3\nlet b = 2\n"
    )
    assert document.place("b", x=1, y=1) is False


def test_the_clipboard_carries_the_file_s_own_format(tmp_path):
    """"as .imgql text, so it pastes anywhere.\""""
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    space = Workspace(path=path)
    text = space.apply("board.copyCards", {"ids": ["program"]})
    # Including for a file nobody has arranged, which is the commonest document
    # there is: one somebody has just opened.
    assert "//@card" in text and "let a = 2" in text


# --------------------------------------------------------------- for agents


def test_an_agent_reads_the_same_manual():
    from voxlogica.ui.manual import manual

    assert manual() == MANUAL.read_text()
