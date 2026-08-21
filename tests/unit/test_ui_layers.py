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


# ------------------------------------------------- the stack, read by the parser


def test_an_array_output_is_read_as_its_elements():
    """And in the reducer's own spelling, not the author's.

    `xs[i]` is sugar: the parser produces `index(xs, i)`, which is the form the
    reducer hashes. Handing back what was typed would mean asking for the hash
    of a string the reducer has to desugar again -- so the canonical spelling is
    the useful answer, not a lossy one.
    """
    from voxlogica.ui import analysis

    (output,) = analysis.outputs('print "scan" [flairs[i], gts[i], masks[i]]')
    assert output.parts == ("index(flairs,i)", "index(gts,i)", "index(masks,i)")


def test_an_ordinary_output_has_no_parts():
    from voxlogica.ui import analysis

    (output,) = analysis.outputs('print "one" flairs[i]')
    assert output.parts == ()
    assert output.expression == "index(flairs,i)"


def test_a_comma_inside_a_call_is_not_a_layer_boundary():
    """Which is the whole reason this is read from the parser and not split."""
    from voxlogica.ui import analysis

    (output,) = analysis.outputs('print "scan" [add(a, b), threshold(c, 0.6)]')
    assert output.parts == ("add(a,b)", "threshold(c,0.6)")


def test_a_stack_of_one_is_still_a_stack():
    from voxlogica.ui import analysis

    (output,) = analysis.outputs('print "scan" [flairs[i]]')
    assert output.parts == ("index(flairs,i)",)


# ----------------------------------------------- a hash per layer, from the reducer

STACK = """\
//@board cols=12 rows=8
//@card id=data kind=code x=0 y=0
let cases  = for k in [0.0, 1.0, 2.0] do k
let flairs = for c in cases do c + 1.0
let masks  = for c in cases do c + 2.0
let i = 1.0
//@card id=scan kind=print index=i style="gray@1.00, red@0.45" x=4 y=0
print "scan" [flairs[i], masks[i]]
"""


def test_a_stack_card_carries_one_expression_per_layer():
    scan = doc.parse(STACK).cards[1]
    assert scan["parts"] == ["index(flairs,i)", "index(masks,i)"]


def test_each_layer_is_bound_to_its_own_node(tmp_path):
    """The whole reason a stack can be drawn: every element has a hash.

    Asked of the real reducer in the document's own context, which is the only
    thing that can answer it -- what `flairs` means here is what makes the hash.
    """
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(STACK)
    space = Workspace(path=path)
    nodes = space.snapshot()["nodes"]

    layers = doc.parse(STACK).cards[1]["parts"]
    hashes = [nodes.get(layer) for layer in layers]
    assert all(hashes), f"every layer needs a node, got {hashes}"
    assert len(set(hashes)) == 2, "two different layers are two different nodes"


def test_two_cards_drawing_the_same_layer_agree_on_its_node(tmp_path):
    """Because a layer is addressed by the expression it is, not by whose it is."""
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(STACK + '//@card id=again kind=print x=8 y=0\nprint "again" flairs[i]\n')
    space = Workspace(path=path)
    nodes = space.snapshot()["nodes"]
    assert nodes["index(flairs,i)"]


def test_a_card_with_one_picture_has_no_parts():
    text = '//@card id=one kind=print x=0 y=0\nprint "one" flairs[i]\n'
    assert "parts" not in doc.parse(text).cards[0]


# ------------------------------------------------------ writing one layer's look

STYLED = """\
//@board cols=12 rows=8
//@card id=scan kind=print x=0 y=0
print "scan" [flairs[i], gts[i], masks[i]]
"""


def _space(tmp_path, text):
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(text)
    return Workspace(path=path)


def test_styling_a_layer_writes_only_that_layer(tmp_path):
    space = _space(tmp_path, STYLED)
    assert space.apply("card.setLayerStyle", {"id": "scan", "at": 2, "opacity": 0.8}) is True
    style = space.snapshot()["cards"][0]["style"]
    assert style[2]["opacity"] == 0.8
    assert [layer["opacity"] for layer in style[:2]] == [1.0, 1.0]


def test_styling_layer_two_of_an_unstyled_card_does_not_become_layer_zero(tmp_path):
    """The list is positional, so it has to be padded before the change lands.

    Without the padding, "set entry two" written into an empty list is entry
    zero, and the scan underneath silently takes the colour of the mask.
    """
    space = _space(tmp_path, STYLED)
    space.apply("card.setLayerStyle", {"id": "scan", "at": 2, "colormap": "red"})
    style = space.snapshot()["cards"][0]["style"]
    assert len(style) == 3
    assert [layer["colormap"] for layer in style] == ["gray", "warm", "red"]


def test_switching_a_layer_off_keeps_its_opacity(tmp_path):
    space = _space(tmp_path, STYLED)
    space.apply("card.setLayerStyle", {"id": "scan", "at": 1, "opacity": 0.35})
    space.apply("card.setLayerStyle", {"id": "scan", "at": 1, "on": False})
    layer = space.snapshot()["cards"][0]["style"][1]
    assert layer["on"] is False
    assert layer["opacity"] == 0.35


def test_a_style_change_never_touches_the_program(tmp_path):
    """The reason the style is a comment: the expression is the cache key, and a
    slider that changes a hash recomputes three hundred megabytes."""
    space = _space(tmp_path, STYLED)
    before = space.snapshot()["cards"][0]["source"]
    space.apply("card.setLayerStyle", {"id": "scan", "at": 0, "opacity": 0.5})
    assert space.snapshot()["cards"][0]["source"] == before


def test_a_negative_layer_is_refused_rather_than_wrapping(tmp_path):
    space = _space(tmp_path, STYLED)
    assert space.apply("card.setLayerStyle", {"id": "scan", "at": -1, "opacity": 0.5}) is False


def test_the_browser_agrees_with_the_server_about_an_unstyled_layer():
    """Two answers to "what colour is layer two" is a card that repaints itself
    when somebody touches a different layer."""
    from pathlib import Path

    from voxlogica.ui import document as document_module

    app = (
        Path(__file__).resolve().parents[2] / "implementation/ui/src/App.svelte"
    ).read_text(encoding="utf-8")
    assert 'style.colormap ?? (at === 0 ? "gray" : "warm")' in app
    assert document_module.default_style(0)["colormap"] == "gray"
    assert document_module.default_style(1)["colormap"] == "warm"
