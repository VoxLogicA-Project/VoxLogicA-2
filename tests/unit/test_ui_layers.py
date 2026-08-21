"""An overlay is an array, and how it looks is a comment.

The board draws a card by evaluating one expression, and a stack of overlays is
one expression: `[flairs[i], gts[i], masks[i]]`. Nothing here invents syntax for
that -- `let f(c)`, `[a, b, c]` and `xs[i]` are the language already, and the
tests at the bottom run them to say so.

What a directive carries is the two things an expression must *not* carry:

`style=` -- how each layer looks. It is out of the program on purpose. The
expression is the cache key, so an opacity inside it would mean that dragging a
slider changes a hash, and changing a hash recomputes the volume. Presentation
has to be free.

`index=` -- the name of the variable this card walks. Master and slave are not
roles and there is no link object: there is `let i = 3`, and every card that
mentions `i` follows it. Which is why two selectors sharing an index is not a
feature -- it is two cards reading one name.
"""

from __future__ import annotations

from voxlogica.ui import document as doc

# ------------------------------------------------------------------ the style


def test_a_style_is_one_entry_per_layer_in_drawing_order():
    read = doc.styles("gray@1.00, blue@0.35, red@0.45")
    assert [layer["colormap"] for layer in read] == ["gray", "blue", "red"]
    assert [layer["opacity"] for layer in read] == [1.0, 0.35, 0.45]
    assert all(layer["on"] for layer in read)


def test_a_layer_switched_off_is_off_and_keeps_its_opacity():
    """Off is not opacity zero: turning it back on must restore what it was."""
    (layer,) = doc.styles("red@0.45!off")
    assert layer["on"] is False
    assert layer["opacity"] == 0.45


def test_an_opacity_nobody_wrote_is_opaque():
    (layer,) = doc.styles("gray")
    assert layer == {"colormap": "gray", "opacity": 1.0, "on": True}


def test_a_style_that_does_not_parse_still_occupies_its_place():
    """The list is positional, so dropping the second entry repaints the third."""
    read = doc.styles("gray@1.00, ?!?, red@0.45")
    assert len(read) == 3
    assert read[1]["colormap"] is None
    assert read[2]["colormap"] == "red"


def test_a_style_survives_being_written_back():
    text = "gray@1.00, blue@0.35!off"
    assert doc.style_text(doc.styles(text)) == text


def test_writing_a_layer_with_nothing_said_about_it_says_something_valid():
    assert doc.style_text([{}]) == "gray@1.00"


# -------------------------------------------------------- and on a card

LAYERED = """\
//@board cols=12 rows=8
//@card id=scan kind=print index=i style="gray@1.00, blue@0.35, red@0.45!off"
print "scan" [flairs[i], gts[i], masks[i]]
//@card id=sheet kind=print index=i view=grid
print "sheet" flairs[i]
"""


def test_a_card_reads_its_index_and_its_style():
    scan = doc.parse(LAYERED).cards[0]
    assert scan["index"] == "i"
    assert [layer["colormap"] for layer in scan["style"]] == ["gray", "blue", "red"]
    assert scan["style"][2]["on"] is False


def test_two_cards_on_one_index_is_all_master_slave_is():
    """No link, no owner: two cards that mention the same name."""
    cards = doc.parse(LAYERED).cards
    assert [card["index"] for card in cards] == ["i", "i"]


def test_a_card_with_no_style_does_not_pretend_to_have_one():
    """One layer needs no style list, and an empty one would style layer zero."""
    sheet = doc.parse(LAYERED).cards[1]
    assert "style" not in sheet
    assert sheet["view"] == "grid"


def test_the_file_comes_back_byte_for_byte():
    """Nothing above may cost the user a diff they did not ask for."""
    document = doc.parse(LAYERED)
    assert document.to_imgql() == LAYERED


def test_the_style_stays_quoted_when_the_line_is_rewritten():
    """Unquoted, its commas and spaces read back as several attributes."""
    document = doc.parse(LAYERED)
    document.place("scan", x=3, y=1)
    written = document.to_imgql()
    assert 'style="gray@1.00, blue@0.35, red@0.45!off"' in written
    assert doc.parse(written).cards[0]["style"][2]["on"] is False


def test_an_attribute_this_build_never_heard_of_survives_anyway():
    text = (
        '//@card id=a kind=print index=i lens="something later" x=0 y=0\n'
        'print "a" flairs[i]\n'
    )
    document = doc.parse(text)
    document.place("a", x=2, y=0)
    assert 'lens="something later"' in document.to_imgql()


# --------------------------------------------- and the language already runs it


def test_the_expressions_this_format_writes_are_the_language(tmp_path):
    """The point of the whole design: no construct here is ours.

    A stack is an array, a case is an index, and one index drives several
    sequences -- which is master and slave, in VoxLogicA, with nothing added.
    """
    from voxlogica.parser import parse_program

    program = tmp_path / "p.imgql"
    program.write_text(
        "let cases  = for k in [0.0, 1.0, 2.0] do k\n"
        "let flairs = for c in cases do c + 1.0\n"
        "let masks  = for c in cases do c + 2.0\n"
        "let i = 1.0\n"
        'print "stack" [flairs[i], masks[i]]\n'
    )
    assert parse_program(program) is not None
