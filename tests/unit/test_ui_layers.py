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


# ------------------------------------------------ a card too small to be used


def test_a_card_declares_the_smallest_size_its_content_can_be_used_at():
    """A volume in two cells is a black rectangle with three letters on it.

    The floor lives in the viewer table -- the same table that decides *which*
    viewer draws a card -- because it is a property of what the card shows. Not
    in the document: the file records the size somebody chose, and a floor is not
    a choice. A stack adds room per layer, since a card that fits the picture but
    not its rows has its controls off the bottom edge.
    """
    from pathlib import Path

    table = (
        Path(__file__).resolve().parents[2] / "implementation/ui/src/lib/viewers/index.js"
    ).read_text(encoding="utf-8")
    assert "needs: { w: 3, h: 3 }" in table
    assert "export function needsFor(" in table
    assert "card?.parts?.length" in table, "a stack needs room for its rows"

    # Asked per card, not folded into the cards array. Measured: computing it
    # into the array rebuilt every card object on every result event, every
    # rebuilt card handed its viewer a fresh `layers` array, and a fresh array
    # made NiiVue reconcile and redraw -- which is what a heavy drag was.
    app = (
        Path(__file__).resolve().parents[2] / "implementation/ui/src/App.svelte"
    ).read_text(encoding="utf-8")
    assert "function floorOf(card)" in app
    assert "cards={workspace.cards}" in app, "the cards array must keep its identity"

    card = (
        Path(__file__).resolve().parents[2]
        / "implementation/ui/src/lib/components/Bento/BentoCard.svelte"
    ).read_text(encoding="utf-8")
    # The document's own minimum wins: an author who wrote one meant it.
    assert "card.minW ?? floor?.w ?? 1" in card


def test_the_floor_bounds_the_gesture_and_rewrites_nobody_s_layout():
    """A constraint on resizing, which is what was asked for -- and no more.

    The first attempt also *granted* the floor: a card below it grew, and other
    cards were moved aside to let it. That did real damage. It pushed a card to
    x=12 on a twelve-column board -- the board fills the viewport, so a wide
    window has columns past `cols`, and a drag may use them, but the board
    rewriting somebody's file may not or the layout opens broken elsewhere. And
    it left a preview behind after an ordinary move, so cards came to rest
    overlapping, which is the one state this board exists never to be in.

    So the floor bounds the *gesture*: `clamp` refuses a resize below it, by
    pointer or by keyboard, and a card whose file says less is drawn at what the
    file says. Cramped is a worse card; overlapping is a broken board.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui"
    bento = (root / "src/lib/components/Bento/Bento.svelte").read_text(encoding="utf-8")
    card = (root / "src/lib/components/Bento/BentoCard.svelte").read_text(encoding="utf-8")

    # The floor reaches the two gestures that change a size, and nothing else.
    assert "const [cw, ch] = clamp(w, h);" in card, "the pointer resize"
    assert "const [w, h] = clamp(size.w + dx, size.h + dy);" in card, "the keyboard resize"
    assert "card.minW ?? floor?.w ?? 1" in card
    assert "card.minH ?? floor?.h ?? 1" in card

    # And nothing grows a card behind the user's back.
    assert "let asked" not in bento, "the automatic growth is gone, with its damage"


def test_the_middle_of_a_card_means_lay_over_it_and_the_edges_mean_next_to_it():
    """The gesture was already taken, which is why it read as nothing happening.

    Dragging a card is arranging it -- that is what it has always meant, and the
    hand goes to the title bar to do it. So "lay over" needs a way to be said
    that arranging cannot be mistaken for, and two say it: alt anywhere inside a
    card, or -- with no modifier at all -- its *middle*, because the middle is
    where you aim when you mean "on this" and the edges are where you aim when
    you mean "beside this".

    The middle is the one that matters, and not because it is more convenient: it
    can be *shown* while the drag is in the air. A modifier cannot, so a modifier
    nobody told you about is a feature nobody has.

    One rule, in one function, asked by both the drawing and the release -- so
    what the board highlighted is what the drop does.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui/src/lib/components/Bento"
    bento = (root / "Bento.svelte").read_text(encoding="utf-8")
    card = (root / "BentoCard.svelte").read_text(encoding="utf-8")

    # The pointer travels with the move *and* the release: cells cannot answer
    # "which card is being aimed at", since a dragged card covers several.
    assert "onpreview?.(gesture.rect, at_(event));" in card
    assert "alt: event.altKey," in card and "clientX: event.clientX," in card

    assert "function layTarget(point, draggedId)" in bento
    rule = bento[bento.index("function layTarget(point, draggedId)") :]
    rule = rule[: rule.index("\n  }")]
    assert "if (point.alt) return other;" in rule
    assert "w / 3" in rule and "h / 3" in rule, "the middle third of each axis"

    # Drawn while in the air, and drawn as taking-in rather than getting-out-of
    # the way: those must not look alike.
    assert "laying = target ? target.id : null;" in bento
    assert "laying={laying === card.id}" in bento
    assert 'content: "lay over";' in card

    # And the release asks the same function, never a second opinion.
    body = bento[bento.index("function commit(card, rect, drop = null)") :]
    body = body[: body.index("\n  }")]
    assert "const under = layTarget(drop, card.id);" in body
    # The preview is dropped the way the board drops one. Reaching for the card's
    # own `onpreview` from here threw, which aborted `commit` before either the
    # merge or the arrangement -- so alt-drop did nothing *and* the card came to
    # rest overlapping, because the card clears its preview only after
    # `oncommit` returns and it never returned.
    assert "onpreview?.(" not in body


def test_a_card_can_be_dragged_onto_another_without_being_selected_first():
    """Reported as "I dragged the brain over the scan and nothing happened".

    And it could not have: the body was `draggable` only when `cardsText` was
    ready, `cardsText` is the *selection* as .imgql, and an unselected card has
    none -- so no drag began at all. Selecting first worked, which is why it
    looked arbitrary rather than broken: press-and-drag in one motion cannot
    work that way, because the selection has to make a round trip before the
    text exists and `dragstart` has already fired by then.

    A DataTransfer may only be written inside `dragstart`, so the text really
    does have to be ready in advance -- but only the *text*. Dropping a card on
    another card needs the ids, and those are known on the spot. So the ids go
    on every drag and the text goes on when it is genuinely this selection's.
    """
    from pathlib import Path

    bento = (
        Path(__file__).resolve().parents[2]
        / "implementation/ui/src/lib/components/Bento/Bento.svelte"
    ).read_text(encoding="utf-8")

    assert "ondragout={(event) => dragOut(card, event)}" in bento, (
        "gated on cardsText, an unselected card is not draggable at all"
    )
    out = bento[bento.index("function dragOut(card, event)") :]
    out = out[: out.index("\n  }")]
    ids = out.index('setData("text/voxlogica-card-ids"')
    text = out.index('setData("text/plain"')
    assert ids < out.index("const ready ="), "the ids are known here and now"
    assert text > out.index("if (!ready) return;"), (
        "the program text is the one thing that has to be ready in advance"
    )

    # And a copy dropped on the sidebar can now arrive without text, so it asks.
    app = (
        Path(__file__).resolve().parents[2] / "implementation/ui/src/App.svelte"
    ).read_text(encoding="utf-8")
    assert "if (payload.copy && !text && payload.ids.length)" in app


def test_an_absorbed_layer_takes_the_colour_of_its_new_position(tmp_path):
    """Measured wrong first: grey over grey, and the second layer invisible.

    A card of one picture has no `style=`, so its only layer was padded with
    *that card's* default -- and position zero is grey. Absorbed as position one
    it carried the grey with it. A layer nobody styled has no opinion, and the
    position it lands in supplies one.
    """
    space = _space(
        tmp_path,
        '//@card id=scan kind=print x=0 y=0\nprint "scan" flair\n'
        '//@card id=mask kind=print x=4 y=0\nprint "mask" tumour\n',
    )
    assert space.apply("card.mergeCard", {"id": "scan", "from": "mask"}) is True
    style = space.snapshot()["cards"][0]["style"]
    assert [layer["colormap"] for layer in style] == ["gray", "warm"]


def test_a_colour_somebody_chose_travels_with_its_layer(tmp_path):
    """The other half: what was said explicitly is not a default to override."""
    space = _space(
        tmp_path,
        '//@card id=scan kind=print x=0 y=0\nprint "scan" flair\n'
        '//@card id=mask kind=print style="red@0.45" x=4 y=0\nprint "mask" tumour\n',
    )
    space.apply("card.mergeCard", {"id": "scan", "from": "mask"})
    style = space.snapshot()["cards"][0]["style"]
    assert (style[1]["colormap"], style[1]["opacity"]) == ("red", 0.45)


def test_laying_a_card_over_another_has_a_named_route_and_not_only_a_gesture():
    """Alt-drag is fast once you know it and invisible until then.

    And a modifier is the part of a gesture most likely to be eaten before it
    arrives -- by a window manager, by a trackpad setting, by the compositor.
    After three rounds of "it does not work" on a mechanism that demonstrably
    sends and applies, the useful thing is a route with a name on it: a menu item
    per card on the page, which cannot quietly not happen.

    Verified in the page: the item appears once per other card, clicking one
    sends `card.mergeCard`, and the target grows two layer rows -- grey
    underneath, warm over it -- with one canvas drawing both.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui/src/lib/components/Bento"
    card = (root / "BentoCard.svelte").read_text(encoding="utf-8")
    bento = (root / "Bento.svelte").read_text(encoding="utf-8")

    assert 'label: `Lay over “${target.title}”`' in card
    assert 'hint: "or alt-drag"' in card, "the gesture is worth teaching from the menu"
    # The board supplies the candidates, because who else is on the page is a
    # question about the board and not about a card.
    assert "layTargets={onmerge" in bento
    assert "onlayover={onmerge" in bento


def test_only_the_grip_carries_the_row_s_drag():
    """Reported as "I cannot drag the sliders".

    The whole row was `draggable`, so a press on the slider began a native drag
    instead of moving the thumb -- and the control people reach for most could
    not be used at all. A row that is draggable everywhere is a row whose
    contents are not usable anywhere.

    Verified in the page: the row carries no `draggable`, the grip carries it,
    and the slider writes nothing while it slides and once when it is released.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "implementation/ui/src/lib/viewers/Layers.svelte"
    ).read_text(encoding="utf-8")

    row = source[source.index('<div\n      class="row"') : source.index('<!-- The only draggable')]
    assert "draggable" not in row, "the row itself must not be draggable"
    grip = source[source.index('class="grip"') :][:400]
    assert 'draggable="true"' in grip
    # And the panel does not hand its presses to the board's own gestures.
    assert "onpointerdown={(event) => event.stopPropagation()}" in source


def test_a_colormap_is_offered_as_the_ramp_it_is():
    """A dot says "red". It cannot say whether the low end is black or white --
    and for a mask laid over a scan that is the whole question, because a ramp
    that starts white erases what is underneath.

    Every name here is one NiiVue actually ships, checked against the package
    rather than guessed: a name it does not know is a layer that silently keeps
    the colour it had.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui"
    ramps = (root / "src/lib/viewers/colormaps.js").read_text(encoding="utf-8")
    shipped = (root / "node_modules/@niivue/niivue/dist/index.js")

    import re

    names = re.findall(r'name: "([a-z]+)"', ramps)
    assert names[0] == "gray", "a scan is grey, and it is what a stack starts from"
    assert len(names) >= 6
    assert all("linear-gradient" in line for line in re.findall(r"css: \"([^\"]+)\"", ramps))

    if shipped.exists():
        text = shipped.read_text(encoding="utf-8", errors="ignore")
        for name in names:
            assert f'"{name}"' in text, f"NiiVue does not ship a colormap called {name}"


def test_a_dragged_card_carries_its_name_and_not_a_photograph_of_itself():
    """The picture is a view, not a thing to pick up.

    Left to itself the browser builds the drag ghost out of the dragged element,
    which for a card means snapshotting a WebGL canvas: an expensive picture of a
    picture that says less than the card's own name. And it privileges the image
    -- the thing seen moving is the volume rather than the card, which is not
    what is being moved. The canvas also offered itself as a drag source of its
    own, so a press on it started the browser's image drag.

    Verified in the page: the canvas carries `draggable="false"`, the ghost handed
    to `setDragImage` is a DIV reading the card's title, and nothing is left in
    the DOM afterwards -- a ghost per drag would be a leak per drag.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui/src"
    volume = (root / "lib/viewers/Volume.svelte").read_text(encoding="utf-8")
    bento = (root / "lib/components/Bento/Bento.svelte").read_text(encoding="utf-8")

    assert 'draggable="false"' in volume
    assert "function setGhost(event, ids)" in bento
    assert "setDragImage(ghost, 12, 12)" in bento
    # Cleared on `dragend`: not on a timer, because a short timer in this file
    # is how a refusal *verdict* used to be spelled and a discipline test keeps
    # it that way -- and not on a frame, since a background tab paints none.
    assert 'window.addEventListener("dragend", () => ghost.remove(), { once: true });' in bento


def test_a_press_on_a_control_never_starts_the_card_s_own_drag():
    """Fixed twice before this, and both times the wrong thing.

    First the layer row was marked undraggable, which changed nothing: `draggable`
    is inherited from the nearest draggable *ancestor*, and the card body was
    always the source.

    Then the body's `dragstart` was made to refuse a press on a control -- by
    asking `event.target`. But **`dragstart` does not fire on what you pressed**:
    it fires on the draggable ancestor, so `event.target` there is the body,
    always, and the question answered "no" however the press began. The test
    passed because it dispatched `dragstart` on the slider directly, which no
    browser ever does. A false positive of my own making, and it cost the user
    two rounds.

    The press is the only moment that knows where it began, so the press records
    it -- captured, because the layer panel stops pointerdown from bubbling and
    presses in the panel are exactly the ones this must see.

    Probed the way it really happens -- press the control, then start the drag on
    the body:

        slider, eye, ramp   refused, carrying nothing
        grip                allowed, and the body adds no card ids to it
        the picture         text/voxlogica-card-ids, as before
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui/src"
    card = (root / "lib/components/Bento/BentoCard.svelte").read_text(encoding="utf-8")
    layers = (root / "lib/viewers/Layers.svelte").read_text(encoding="utf-8")

    # Recorded on the press, in the capture phase, or the panel's own
    # `stopPropagation` hides exactly the presses that matter.
    assert "onpointerdowncapture={(event) => (pressedOn = event.target)}" in card
    start = card[card.index("      ondragstart={(event) => {") :]
    start = start[: start.index("      }}")]
    assert "const from = pressedOn ?? event.target;" in start, (
        "asking the dragstart's own target answers about the body, always"
    )
    assert 'from?.closest?.(".layers")' in start
    assert 'from.closest("[data-grip]")' in start
    assert 'from?.closest?.("input, button, select, textarea, a, label")' in start
    assert "data-grip" in layers


def test_the_picture_follows_the_thumb_and_nothing_jumps_back():
    """Two faults with one cause: the opacity lived only in the document.

    So while a finger was on the slider nothing else knew -- the volume kept its
    old opacity until a round trip finished, and the thumb was held by a local
    copy that was *dropped on release*, which put the old value back for exactly
    one round trip. That is the jump home and then the jump again.

    The live value now sits where the layers a viewer draws are assembled, so it
    reaches the volume by the same path the committed one does and there is no
    second route to disagree with the first. It is cleared when the document
    catches up, not when the finger lifts.

    Measured in the page, eleven steps of a slide: the readout followed the
    thumb, 1582 WebGL draws happened while sliding (the picture, following), no
    document writes went out during it, one `setLayerStyle` on release, and the
    thumb read 30 during, at release, and after the round trip.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "implementation/ui/src"
    app = (root / "App.svelte").read_text(encoding="utf-8")
    layers = (root / "lib/viewers/Layers.svelte").read_text(encoding="utf-8")

    # Held where the layers are assembled, and read by the thing that draws.
    assert "let live = $state({});" in app
    assert "opacity: live[`${card.id}:${at}`] ?? style.opacity ?? 1," in app
    assert "onlive={(at, opacity) => (live[`${card.id}:${at}`] = opacity)}" in app
    # Cleared by the document catching up -- never by the finger lifting.
    assert "Math.abs(settled - live[key]) < 0.005" in app

    # One source for the thumb, so it cannot disagree with the picture.
    assert "const opacityOf = (layer) => layer.opacity;" in layers
    assert "oninput={(event) => onlive?.(at, event.currentTarget.value / 100)}" in layers
    # And the document is written once, when the finger lifts.
    assert "onchange={(event) =>\n            cardActions.setLayerStyle(" in layers
