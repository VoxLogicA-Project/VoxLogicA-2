"""Which binding a card is about.

A card holds a fragment, and a fragment may declare several names. The rule --
the last one it declares, unless somebody said otherwise -- is answered on the
server so that a person in a browser and an agent over MCP are looking at the
same card. Two implementations of "what is this card about" would disagree on
the day one of them was updated.
"""

from __future__ import annotations

from voxlogica.ui import document as doc
from voxlogica.ui.workspace import Workspace

FRAGMENT = """\
//@board cols=12 rows=8
//@card id=c1 kind=code x=0 y=0
let scratch = 1
let step = scratch + 1
let answer = step * 2
"""


def only(text: str) -> dict:
    return doc.parse(text).cards[0]


# ---------------------------------------------------------------- the default


def test_a_fragment_is_about_the_last_name_it_declares():
    """Not the last line of a *file*, which moves when somebody appends to it.

    A fragment's boundary was drawn by hand, and it reads as scaffolding
    building toward its final name: the earlier bindings are the working.
    """
    assert only(FRAGMENT)["focus"] == "answer"


def test_a_card_declaring_nothing_is_about_the_node_it_is_bound_to():
    text = '//@card id=c1 kind=result node=mask x=0 y=0\n'
    assert only(text)["focus"] == "mask"


def test_a_card_about_nothing_says_so():
    assert only("//@card id=c1 kind=note x=0 y=0\n")["focus"] is None


def test_a_stated_focus_wins_over_the_default():
    text = FRAGMENT.replace("id=c1 kind=code", "id=c1 kind=code focus=step")
    assert only(text)["focus"] == "step"


def test_the_default_follows_the_text():
    """Nothing is stored, so appending a binding moves the focus with it -- which
    is what "the last one" has to mean for it to be a default at all."""
    assert only(FRAGMENT + "let later = answer + 1\n")["focus"] == "later"


# --------------------------------------------------------------- round trip


def test_setting_a_focus_survives_the_file():
    document = doc.parse(FRAGMENT)
    assert document.set_attr("c1", "focus", "step") is True
    written = document.to_imgql()
    assert "focus=step" in written
    assert doc.parse(written).cards[0]["focus"] == "step"


def test_clearing_a_focus_returns_to_the_default_rather_than_to_nothing():
    """A card about nothing would be a card with no reason to have a Run
    button. Clearing is "back to the last binding", not "none"."""
    document = doc.parse(FRAGMENT)
    document.set_attr("c1", "focus", "step")
    document.set_attr("c1", "focus", None)
    written = document.to_imgql()
    assert "focus=" not in written
    assert doc.parse(written).cards[0]["focus"] == "answer"


def test_an_untouched_document_is_not_rewritten_by_being_read():
    document = doc.parse(FRAGMENT)
    _ = document.cards
    assert document.to_imgql() == FRAGMENT


# ------------------------------------------------------------- through actions


def test_the_action_sets_and_clears_it(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(FRAGMENT)
    workspace = Workspace(path=path)

    assert workspace.apply("card.setFocus", {"id": "c1", "focus": "scratch"}) is True
    assert workspace.document.cards[0]["focus"] == "scratch"

    assert workspace.apply("card.setFocus", {"id": "c1"}) is True
    assert workspace.document.cards[0]["focus"] == "answer"


def test_the_action_refuses_a_card_that_is_not_there(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(FRAGMENT)
    workspace = Workspace(path=path)
    assert workspace.apply("card.setFocus", {"id": "nope", "focus": "x"}) is False


def test_a_derived_output_card_is_about_what_it_prints():
    cards = doc.parse('let a = 2\nlet s = a + 1\nprint "total" s\n').cards
    assert [card["focus"] for card in cards] == ["s", "s"]
