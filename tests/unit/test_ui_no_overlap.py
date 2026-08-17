"""Cards never share a cell, and that is an invariant rather than a habit.

The board's whole model rests on it: placement is *refused* rather than
resolved, and the arithmetic that makes room for a drag begins by assuming
nothing overlaps yet. The moment two cards share a cell that assumption is
false, and every gesture afterwards behaves inexplicably -- cards refuse to move
anywhere, the avoidance stops working, and nothing on screen says why.

It was not an invariant. `canPlace` in the board asked `visible`, and `visible`
is filtered by *focus* -- so maximizing a focused card found an empty board and
grew over everything on it. The layout was then genuinely overlapping, on disk,
and stayed that way. Two fixes, at two levels, because the rule needs both:

- the board no longer lets a rendering filter reach its geometry
  (`occupants` vs `visible`, checked in test_ui_design_system_discipline.py);
- and the *document* refuses an overlapping placement, so no agent and no
  future gesture can write one either.
"""

from __future__ import annotations

from voxlogica.ui import analysis
from voxlogica.ui.document import parse

TWO = """\
//@board cols=12 rows=8
//@card id=a kind=code x=0 y=0 w=4 h=3
let a = 1
//@card id=b kind=code x=5 y=0 w=4 h=3
let b = 2
"""

#: What the bug actually produced, and what somebody's file now contains.
TANGLED = """\
//@board cols=12 rows=8
//@card id=big kind=code x=0 y=0 w=15 h=11
let y = 5
//@card id=one kind=code x=4 y=0 w=5 h=3
let a = 1
//@card id=two kind=code x=4 y=6 w=4 h=3
let w = 2
"""


def fresh(text: str = TWO):
    return parse(text)


# ------------------------------------------------------------------- noticing


def test_overlaps_are_found_in_pairs():
    assert analysis.overlapping(fresh(TANGLED).cards) == [("big", "one"), ("big", "two")]


def test_a_tidy_board_has_none():
    assert analysis.overlapping(fresh().cards) == []


def test_cards_on_different_pages_share_nothing():
    """Two cards at the same cell on different pages are not in each other's
    way -- that is what a page is."""
    document = fresh()
    document.place("b", page=1)
    document.place("b", x=0, y=0)
    assert analysis.overlapping(document.cards) == []


def test_an_unsized_card_is_left_out_of_the_question():
    """Until it has been measured, the document does not know what it covers.

    Guessing small is what let other cards be grown over it; unknown is not the
    same as tiny. The answer is not a better guess -- it is that the browser
    reports the measurement, below.
    """
    text = TWO + "//@card id=c kind=code x=0 y=0\nlet c = 3\n"
    assert analysis.overlapping(parse(text).cards) == []


# ------------------------------------------------- every card has a footprint


def test_a_measured_card_becomes_visible_to_the_invariant():
    """The hole that made all of this feel like whack-a-mole.

    A self-sizing card had a size only in the browser, so the document could
    not keep cards off it -- and half the ways to overlap went through exactly
    that. The board reports what it measured, and from then on the card is a
    rectangle like any other.
    """
    document = parse(
        "//@board cols=12 rows=8\n"
        "//@card id=p kind=code x=0 y=0\nlet a = 1\n"
        "//@card id=b kind=code x=6 y=0 w=4 h=3\nlet b = 2\n"
    )
    assert document.place("b", x=1, y=1) is True, "nothing known, nothing refused"

    document = parse(
        "//@board cols=12 rows=8\n"
        "//@card id=p kind=code x=0 y=0\nlet a = 1\n"
        "//@card id=b kind=code x=6 y=0 w=4 h=3\nlet b = 2\n"
    )
    assert document.measured("p", 5, 4) is True
    assert document.place("b", x=1, y=1) is False, "the card is known now"


def test_measuring_leaves_the_card_the_content_s_to_size():
    """`auto` says where a size came from, not whether there is one."""
    document = parse("//@board cols=9 rows=8\n//@card id=p kind=code x=0 y=0\nlet a = 1\n")
    document.measured("p", 5, 4)
    card = document.cards[0]
    assert (card["w"], card["h"]) == (5, 4)
    assert card["auto"] is True


def test_sizing_a_card_by_hand_takes_it_away_from_the_content():
    """This used to be implicit -- carrying w/h at all meant "not auto" -- and
    that signal died the day every card started carrying them. A card that
    silently re-measured itself back over the size somebody just chose would be
    the worst of both."""
    document = parse("//@board cols=9 rows=8\n//@card id=p kind=code x=0 y=0\nlet a = 1\n")
    document.measured("p", 5, 4)
    document.place("p", w=7)
    assert document.cards[0]["auto"] is False


def test_moving_a_card_leaves_it_the_content_s_to_size():
    """Only sizing it by hand is a claim about its size; moving it is not."""
    document = parse("//@board cols=9 rows=8\n//@card id=p kind=code x=0 y=0\nlet a = 1\n")
    document.measured("p", 5, 4)
    document.place("p", x=2, y=1)
    assert document.cards[0]["auto"] is True


def test_a_measurement_that_would_overlap_is_refused():
    """The card is drawn at what it measured and stored at the last size
    everyone agreed on -- a smaller lie than a board whose rules do not hold."""
    document = fresh()
    assert document.measured("a", 20, 20) is False


# ------------------------------------------------------------------ refusing


def test_a_move_onto_another_card_is_refused():
    assert fresh().place("b", x=1, y=1) is False


def test_a_resize_over_another_card_is_refused():
    """The shape the bug took: something grew rather than moved."""
    assert fresh().place("a", w=20) is False


def test_a_move_to_free_cells_is_allowed():
    assert fresh().place("b", x=5, y=4) is True


def test_growing_into_free_cells_is_allowed():
    assert fresh().place("a", w=5) is True


def test_a_refused_placement_changes_nothing():
    """Refusing has to mean refusing: a half-applied move would leave the card
    somewhere nobody asked for."""
    document = fresh()
    before = document.to_imgql()
    assert document.place("b", x=1, y=1) is False
    assert document.to_imgql() == before


def test_an_unsized_card_may_still_be_moved():
    """It cannot be proven safe, and refusing every move of a card the board
    sizes would make those cards unusable."""
    document = parse("//@board cols=9 rows=8\n//@card id=c kind=code x=0 y=0\nlet c = 1\n")
    assert document.place("c", x=3, y=3) is True


# ------------------------------------------------------------------ repairing


def test_untangling_a_broken_document_makes_it_whole():
    """Somebody's file is already in this state, so noticing is not enough."""
    document = fresh(TANGLED)
    moved = document.untangle()
    assert moved == ["one", "two"]
    assert analysis.overlapping(document.cards) == []


def test_untangling_moves_nobody_who_did_not_have_to_move():
    """A repair, not a layout engine: the first card to claim a cell keeps it."""
    document = fresh(TANGLED)
    document.untangle()
    big = next(c for c in document.cards if c["id"] == "big")
    assert (big["x"], big["y"]) == (0, 0)


def test_untangling_a_tidy_board_does_nothing():
    document = fresh()
    assert document.untangle() == []
    assert document.dirty is False


def test_the_repair_is_reachable_as_an_action(tmp_path):
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(TANGLED)
    workspace = Workspace(path=path)
    assert workspace.apply("board.untangle", {}) == ["one", "two"]
    assert workspace.snapshot()["issues"]["overlaps"] == []


def test_the_workspace_says_when_a_document_arrived_overlapping(tmp_path):
    """Facts rather than a dialogue -- the same treatment cycles and duplicate
    names get, because it is the same kind of thing: something true about the
    document that the user should be told rather than asked about."""
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(TANGLED)
    issues = Workspace(path=path).snapshot()["issues"]
    assert issues["overlaps"] == [["big", "one"], ["big", "two"]]


# ------------------------------------------------- one gesture, one arrangement


def test_a_resize_and_the_cards_it_displaces_land_together():
    """The reported failure, exactly: "they avoided, then came back overlapped".

    One drag is one arrangement -- the card under the finger and everyone who
    stepped aside for it. Applied one at a time it passes through states that
    really do overlap (grow a card before its neighbour has moved), so a
    per-step check refuses the resize and lets the neighbour move anyway.
    """
    document = parse(
        "//@board cols=20 rows=8\n"
        "//@card id=a kind=code x=0 y=0 w=4 h=3\nlet a = 1\n"
        "//@card id=b kind=code x=4 y=0 w=4 h=3\nlet b = 2\n"
    )
    assert document.arrange([{"id": "a", "w": 8}, {"id": "b", "x": 8}]) is True
    assert analysis.overlapping(document.cards) == []
    cards = {card["id"]: card for card in document.cards}
    assert cards["a"]["w"] == 8 and cards["b"]["x"] == 8


def test_an_arrangement_that_would_overlap_is_refused_whole():
    """All or nothing: half a gesture leaves a layout nobody asked for."""
    document = parse(
        "//@board cols=20 rows=8\n"
        "//@card id=a kind=code x=0 y=0 w=4 h=3\nlet a = 1\n"
        "//@card id=b kind=code x=4 y=0 w=4 h=3\nlet b = 2\n"
    )
    before = document.to_imgql()
    assert document.arrange([{"id": "a", "w": 8}]) is False
    assert document.to_imgql() == before


def test_an_arrangement_naming_a_card_that_is_not_there_is_refused():
    document = fresh()
    before = document.to_imgql()
    assert document.arrange([{"id": "a", "x": 6}, {"id": "ghost", "x": 0}]) is False
    assert document.to_imgql() == before


def test_two_cards_may_swap_places_in_one_gesture():
    """Impossible step by step -- either half lands on the other -- and the
    plainest demonstration that the result is what is checked."""
    document = fresh()
    assert document.arrange([{"id": "a", "x": 5}, {"id": "b", "x": 0}]) is True
    cards = {card["id"]: card for card in document.cards}
    assert (cards["a"]["x"], cards["b"]["x"]) == (5, 0)
