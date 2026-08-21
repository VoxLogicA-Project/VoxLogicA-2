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


# ------------------------------------------------------ separate, and separable

MERGEABLE = """\
//@board cols=12 rows=8
//@card id=scan kind=print x=0 y=0 style="gray@1.00, blue@0.35"
print "scan" [flairs[i], gts[i]]
//@card id=found kind=print x=4 y=0 style="red@0.45"
print "found" masks[i]
"""


def test_the_elements_are_cut_from_the_text_and_not_respelled():
    """`xs[i]` is sugar. Rebuilding the line from the parser would write the
    sugar out, so reordering two layers would rewrite lines nobody touched."""
    before, elements, after = doc.array_of('print "scan" [flairs[i], masks[i]]\n')
    assert elements == ["flairs[i]", "masks[i]"]
    assert before.endswith("[") and after.startswith("]")


def test_a_comma_inside_a_call_or_a_string_is_not_a_boundary():
    _b, elements, _a = doc.array_of('print "s" [add(a, b), "x, y", [c, d]]\n')
    assert elements == ["add(a, b)", '"x, y"', "[c, d]"]


def test_a_print_that_is_not_an_array_is_left_alone():
    assert doc.array_of('print "one" flairs[i]\n') is None


def test_reordering_moves_the_style_with_the_layer(tmp_path):
    """The bug this exists to prevent: styles find their layer by position, so
    moving an element and not its style repaints the whole stack."""
    space = _space(tmp_path, MERGEABLE)
    assert space.apply("card.moveLayer", {"id": "scan", "at": 1, "to": 0}) is True
    scan = space.snapshot()["cards"][0]
    assert scan["parts"] == ["index(gts,i)", "index(flairs,i)"]
    assert [layer["colormap"] for layer in scan["style"]] == ["blue", "gray"]
    assert [layer["opacity"] for layer in scan["style"]] == [0.35, 1.0]


def test_reordering_rearranges_and_does_not_rewrite(tmp_path):
    space = _space(tmp_path, MERGEABLE)
    space.apply("card.moveLayer", {"id": "scan", "at": 1, "to": 0})
    assert 'print "scan" [gts[i], flairs[i]]' in space.snapshot()["cards"][0]["source"]


def test_dropping_a_card_on_another_makes_it_a_layer(tmp_path):
    space = _space(tmp_path, MERGEABLE)
    assert space.apply("card.mergeCard", {"id": "scan", "from": "found"}) is True
    cards = space.snapshot()["cards"]
    assert [card["id"] for card in cards] == ["scan"], "the dropped card became a row"
    assert cards[0]["parts"] == ["index(flairs,i)", "index(gts,i)", "index(masks,i)"]
    # And it kept the colour it had.
    assert [layer["colormap"] for layer in cards[0]["style"]] == ["gray", "blue", "red"]


def test_a_card_of_one_picture_becomes_an_array_when_something_lands_on_it(tmp_path):
    space = _space(tmp_path, MERGEABLE)
    space.apply("card.mergeCard", {"id": "found", "from": "scan"})
    source = space.snapshot()["cards"][0]["source"]
    assert 'print "found" [masks[i], flairs[i], gts[i]]' in source


def test_a_layer_dragged_out_becomes_a_card_again(tmp_path):
    space = _space(tmp_path, MERGEABLE)
    made = space.apply("card.splitLayer", {"id": "scan", "at": 1, "x": 8, "y": 0})
    assert made
    cards = {card["id"]: card for card in space.snapshot()["cards"]}
    assert 'print "gts" gts[i]' in cards[made]["source"]
    assert cards[made]["style"][0]["colormap"] == "blue", "it wears the colour it had"
    assert cards["scan"]["parts"] == ["index(flairs,i)"]


def test_merging_and_splitting_are_each_other(tmp_path):
    """A gesture that does not undo itself is a gesture nobody trusts."""
    space = _space(tmp_path, MERGEABLE)
    before = space.snapshot()["cards"][0]["source"]
    space.apply("card.mergeCard", {"id": "scan", "from": "found"})
    space.apply("card.splitLayer", {"id": "scan", "at": 2, "newId": "found", "x": 4, "y": 0})
    after = {card["id"]: card for card in space.snapshot()["cards"]}
    assert after["scan"]["source"] == before
    assert 'masks[i]' in after["found"]["source"]
    assert after["found"]["style"][0]["colormap"] == "red"


def test_the_last_layer_cannot_be_taken_out(tmp_path):
    """A stack of nothing is not a card, and emptying one is a different act."""
    space = _space(tmp_path, MERGEABLE)
    assert space.apply("card.splitLayer", {"id": "found", "at": 0}) is False


def test_a_card_cannot_be_dropped_on_itself(tmp_path):
    space = _space(tmp_path, MERGEABLE)
    assert space.apply("card.mergeCard", {"id": "scan", "from": "scan"}) is False


# --------------------------------------------------------------- the selector

WALKED = """\
//@board cols=12 rows=8
//@card id=data kind=code x=0 y=0
let cases  = for k in [0.0, 1.0, 2.0, 3.0] do k
let flairs = for c in cases do c + 1.0
let masks  = for c in cases do c + 2.0
let i = 1
//@card id=scan kind=print index=i style="gray@1.00, red@0.45" x=4 y=0
print "scan" [flairs[i], masks[i]]
//@card id=sheet kind=print index=i x=8 y=0
print "sheet" flairs[i]
"""


def test_a_card_that_owns_an_index_says_what_it_walks_along():
    """`flairs[i]` is a step along `flairs`, which is where the walk ends."""
    cards = {card["id"]: card for card in doc.parse(WALKED).cards}
    assert cards["scan"]["over"] == "flairs"
    assert cards["sheet"]["over"] == "flairs"


def test_a_card_with_no_index_is_not_walking_anything():
    text = '//@card id=one kind=print x=0 y=0\nprint "one" flairs[i]\n'
    assert "over" not in doc.parse(text).cards[0]


def test_walking_the_index_edits_one_line(tmp_path):
    space = _space(tmp_path, WALKED)
    assert space.apply("card.setIndex", {"id": "scan", "value": 3}) is True
    source = space.snapshot()["source"]
    assert "let i = 3" in source
    assert "let i = 1" not in source


def test_every_card_on_that_index_moved_with_it(tmp_path):
    """There is no link to update: they mention the same name."""
    space = _space(tmp_path, WALKED)
    space.apply("card.setIndex", {"id": "sheet", "value": 2})
    cards = {card["id"]: card for card in space.snapshot()["cards"]}
    assert cards["scan"]["index"] == cards["sheet"]["index"] == "i"
    assert "let i = 2" in space.snapshot()["source"]


def test_the_sequence_is_bound_so_its_length_can_be_known(tmp_path):
    space = _space(tmp_path, WALKED)
    assert space.snapshot()["nodes"].get("flairs")


def test_an_index_written_as_a_float_stays_one(tmp_path):
    """The file is somebody's. `2.0` is not an invitation to write `3`."""
    space = _space(tmp_path, WALKED.replace("let i = 1", "let i = 1.0"))
    space.apply("card.setIndex", {"id": "scan", "value": 2})
    assert "let i = 2.0" in space.snapshot()["source"]


def test_an_index_bound_to_arithmetic_is_not_overwritten(tmp_path):
    """Overwriting it would be deleting somebody's work to record a click."""
    space = _space(tmp_path, WALKED.replace("let i = 1", "let i = add(j, 1)"))
    assert space.apply("card.setIndex", {"id": "scan", "value": 2}) is False
    assert "let i = add(j, 1)" in space.snapshot()["source"]


def test_a_card_that_owns_no_index_cannot_walk_one(tmp_path):
    space = _space(tmp_path, WALKED)
    assert space.apply("card.setIndex", {"id": "data", "value": 2}) is False


# ------------------------------- the selector before anything has been computed


def test_where_the_walk_is_comes_from_the_text(tmp_path):
    """A selector has to work on a board nobody has run yet.

    `let g = 3` is a node like any other, so before a run it has no value. Read
    the value and the chevrons say 0 while the file plainly says 3 -- and a
    selector you cannot trust is a selector you cannot use to *start* the run,
    which is the one thing it is for.
    """
    cards = {card["id"]: card for card in doc.parse(WALKED).cards}
    assert cards["scan"]["at"] == 1
    assert cards["sheet"]["at"] == 1


def test_the_walk_follows_a_step(tmp_path):
    space = _space(tmp_path, WALKED)
    space.apply("card.setIndex", {"id": "scan", "value": 3})
    cards = {card["id"]: card for card in space.snapshot()["cards"]}
    assert cards["scan"]["at"] == cards["sheet"]["at"] == 3


def test_a_float_index_still_reads_as_a_position():
    cards = {card["id"]: card for card in doc.parse(WALKED.replace("let i = 1", "let i = 2.0")).cards}
    assert cards["scan"]["at"] == 2


def test_the_navigation_is_not_gated_on_there_being_a_picture():
    """The bug this replaces: chevrons rendered only inside the branch that had
    layers to draw, so a board of uncomputed cards had no way to walk."""
    from pathlib import Path

    app = (
        Path(__file__).resolve().parents[2] / "implementation/ui/src/App.svelte"
    ).read_text(encoding="utf-8")
    body = app[app.index("{:else if viewer.result}") : app.index("{:else if viewer.source}")]
    walk = body.index("<Walk {card}")
    drawing = body.index("{#if drawing}")
    # The Walk sits beside the viewer, in the same box, not inside the branch
    # that chose whether there was anything to draw.
    assert body.index("{@const walk = card.index ? walkOf(card) : null}") < drawing
    assert "{#if walk}" in body and walk > drawing
    assert "{#if drawing && stack}" not in app


def test_the_navigation_floats_over_a_picture_and_takes_a_line_over_anything_else():
    """Two printed things in the same place, and it was measured that way.

    On a card whose value is a number, the tabs occupied 432-457 and the value
    432-451 -- the same region. Floating chrome over a *volume* is right: the
    card exists to show the volume. Over text it is just overprinting.

    Pinning the strip to the bottom instead was the next attempt and it still
    collided on a card three cells tall, so the strip goes in the *flow*: the
    content box is shorter by exactly that much and no arrangement of card size
    and content can put them on top of each other.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui"
    app = (root / "src/App.svelte").read_text(encoding="utf-8")
    walk = (root / "src/lib/viewers/Walk.svelte").read_text(encoding="utf-8")

    body = app[app.index("{:else if viewer.result}") : app.index("{:else if viewer.source}")]
    over = body[body.index('<div class="over">') : body.index('{#if walk}\n              <ResultSubscription')]
    # Floating only where there is a picture to float over, and inside the box
    # that holds it.
    assert "{#if walk && drawing}" in over
    # The strip is a sibling of that box, not a child: in the flow.
    assert body.index("{#if walk && !drawing}") > body.index("</div>")
    assert "floating={false}" in body

    # And the strip is not positioned out of the flow after all.
    strip = walk[walk.index("  .strip {") :]
    strip = strip[: strip.index("}")]
    assert "position: absolute" not in strip
    assert "flex: none" in strip
