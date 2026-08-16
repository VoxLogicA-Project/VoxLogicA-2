"""A file nobody has arranged: the program, and the outputs it declares.

`print` and `save` are what a program *says* it produces, with names its author
chose. So they are what a board should show when there is no layout to read --
and the two are kept apart, because a print is a value shown and a save is an
effect with a destination.

The rule these tests are really defending is the one that is invisible when it
breaks: **opening a file must not modify it.** These cards are derived and live
in the list and nowhere else until the first edit.
"""

from __future__ import annotations

from voxlogica.ui import analysis
from voxlogica.ui.document import parse

PLAIN = "let a = 2\nlet s = a + 1\n"
WITH_OUTPUTS = 'let a = 2\nlet s = a + 1\nprint "total" s\nsave "out.nii" s\n'


def kinds(text: str) -> list[str]:
    return [card["kind"] for card in parse(text).cards]


# ------------------------------------------------------------------- reading


def test_print_and_save_are_read_from_the_real_parser():
    found = analysis.outputs(WITH_OUTPUTS)
    assert [(item.operation, item.label, item.binding) for item in found] == [
        ("print", "total", "s"),
        ("save", "out.nii", "s"),
    ]


def test_a_print_of_something_larger_than_a_name_is_left_unbound():
    """Honest rather than guessed at: binding it needs a hash for a
    sub-expression, which is a separate piece of machinery."""
    found = analysis.outputs('let a = 2\nprint "twice" plus(a,a)\n')
    assert len(found) == 1
    assert found[0].binding is None
    assert found[0].expression == "plus(a,a)"


def test_a_document_that_does_not_parse_declares_nothing():
    """The normal case mid-edit, and the same rule `analyse` follows."""
    assert analysis.outputs("print ") == []
    assert analysis.outputs("!!!") == []


def test_the_word_print_inside_a_string_is_not_an_output():
    """What a regular expression would have got wrong."""
    assert analysis.outputs('let a = "print \\"x\\" y"\n') == []


# -------------------------------------------------------------------- boards


def test_a_program_with_no_outputs_is_still_one_card():
    """The degenerate case, unchanged: sized to its content."""
    cards = parse(PLAIN).cards
    assert len(cards) == 1
    assert cards[0]["id"] == "program"
    assert cards[0]["auto"] is True


def test_each_output_becomes_its_own_card():
    cards = parse(WITH_OUTPUTS).cards
    assert kinds(WITH_OUTPUTS) == ["code", "print", "save"]
    assert [card["title"] for card in cards] == ["Program", "total", "out.nii"]
    assert all(card["node"] == "s" for card in cards[1:])


def test_the_program_stops_measuring_itself_once_it_has_neighbours():
    """An auto card's width is not known until it is drawn, and a layout that
    cannot say where its second column starts would put one card on top of
    another the first time a program was wide."""
    cards = parse(WITH_OUTPUTS).cards
    assert cards[0]["auto"] is False
    assert cards[0]["w"] and cards[0]["h"]


def test_no_two_derived_cards_overlap():
    program = 'let a = 1\nprint "one" a\nprint "two" a\nsave "three.nii" a\n'
    boxes = [
        (card["x"], card["y"], card.get("w", 1), card.get("h", 1))
        for card in parse(program).cards
    ]
    for i, (ax, ay, aw, ah) in enumerate(boxes):
        for bx, by, bw, bh in boxes[i + 1:]:
            apart = ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
            assert apart, f"{boxes}"


def test_derived_cards_have_distinct_ids():
    program = 'let a = 1\nprint "one" a\nprint "two" a\n'
    ids = [card["id"] for card in parse(program).cards]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------- and the file is untouched


def test_opening_a_file_does_not_rewrite_it():
    """The property worth more than any of the above.

    Somebody who opens a program to look at it and closes it again finds their
    file exactly as they left it, byte for byte -- including the ones that had
    outputs and therefore grew cards.
    """
    for text in (PLAIN, WITH_OUTPUTS, "", "// just a comment\n"):
        document = parse(text)
        _ = document.cards  # deriving must not be a mutation
        assert document.to_imgql() == text
        assert document.annotated is False
