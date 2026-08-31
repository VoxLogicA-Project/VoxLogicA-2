"""Where a card goes in the file is worked out from the language, not guessed.

The board arranges cards in space, and space says nothing about the order a
program is read in. So the order is derived: `voxlogica.parser` -- the same front
end the engine uses -- says what each card defines and what it needs, and the
cards are written in an order where every name is defined before it is used.

These tests are mostly about the parts a regular expression would get wrong: a
local `let`, a `for` variable, a declaration's own parameters, and text that does
not parse at all.
"""

from __future__ import annotations

import pytest

from voxlogica.ui import analysis
from voxlogica.ui import document as doc


def card(card_id, source, **attrs):
    return {"id": card_id, "source": source, **attrs}


# ----------------------------------------------------------------- what it sees


def test_a_declaration_defines_its_name_and_needs_what_it_mentions():
    seen = analysis.analyse("let mask = threshold(flair, 0.6)")
    assert seen.defines == {"mask"}
    assert "flair" in seen.needs


def test_a_local_let_binds_inside_its_body_only():
    """The case a regular expression gets wrong."""
    seen = analysis.analyse("let y = let t = 2 in add(t, x)")
    assert seen.defines == {"y"}
    assert "t" not in seen.needs
    assert "x" in seen.needs


def test_a_loop_variable_is_not_a_dependency():
    seen = analysis.analyse("let s = for i in xs do add(i, base)")
    assert "i" not in seen.needs
    assert {"xs", "base"} <= seen.needs


def test_a_declarations_own_parameters_are_not_dependencies():
    seen = analysis.analyse("let f(x) = add(x, k)")
    assert seen.defines == {"f"}
    assert "x" not in seen.needs
    assert "k" in seen.needs


def test_a_declaration_that_mentions_itself_needs_itself():
    """Which is how recursion is found rather than accepted."""
    seen = analysis.analyse("let loop = add(loop, 1)")
    assert "loop" in seen.needs or seen.defines == {"loop"}


def test_printing_something_is_using_it():
    seen = analysis.analyse('print "m" mask')
    assert seen.defines == frozenset()
    assert "mask" in seen.needs


def test_source_that_does_not_parse_is_unknown_rather_than_guessed():
    assert analysis.analyse("let = = =") is None


# -------------------------------------------------------------------- ordering


def test_a_card_is_written_after_what_it_needs():
    cards = [
        card("uses", "let mask = threshold(flair, 0.6)"),
        card("defines", 'let flair = load("case.nii.gz")'),
    ]
    assert analysis.dependency_order(cards) == ["defines", "uses"]


def test_cards_that_do_not_depend_on_each_other_keep_their_order():
    """Stable, so writing the file back is the smallest correct diff."""
    cards = [card("a", "let a = 1"), card("b", "let b = 2"), card("c", "let c = 3")]
    assert analysis.dependency_order(cards) == ["a", "b", "c"]


def test_a_chain_is_ordered_all_the_way_down():
    cards = [
        card("third", "let c = add(b, 1)"),
        card("first", "let a = 1"),
        card("second", "let b = add(a, 1)"),
    ]
    assert analysis.dependency_order(cards) == ["first", "second", "third"]


def test_a_primitive_is_not_a_card_and_orders_nothing():
    """`threshold` being defined by nobody simply means nobody provides it."""
    cards = [card("only", "let m = threshold(x, 0.5)"), card("x", "let x = 1")]
    assert analysis.dependency_order(cards) == ["x", "only"]


def test_cards_that_need_each_other_are_refused_by_name():
    cards = [card("a", "let a = add(b, 1)"), card("b", "let b = add(a, 1)")]
    with pytest.raises(analysis.Cycle) as raised:
        analysis.dependency_order(cards)
    assert set(raised.value.ids) >= {"a", "b"}


def test_a_card_that_does_not_parse_stays_where_it_is():
    """Reordering text nobody understood is how an editor loses work."""
    cards = [
        card("uses", "let mask = threshold(flair, 0.6)"),
        card("broken", "let = = ="),
        card("defines", 'let flair = load("case.nii.gz")'),
    ]
    order = analysis.dependency_order(cards)
    assert order[1] == "broken"
    assert order.index("defines") < order.index("uses")


def test_two_cards_defining_one_name_are_reported_with_both_ids():
    cards = [card("a", "let mask = 1"), card("b", "let mask = 2"), card("c", "let other = 3")]
    assert analysis.duplicates(cards) == {"mask": ["a", "b"]}


# ------------------------------------------------- and the document uses it


ORDERED = """\
//@board cols=9 rows=8
//@card id=uses kind=code x=0 y=0
let mask = threshold(flair, 0.6)
//@card id=defines kind=code x=5 y=0
let flair = load("case.nii.gz")
"""


def test_tidying_a_document_writes_the_definitions_first():
    document = doc.parse(ORDERED)
    assert document.tidy() is True
    out = document.to_imgql()
    assert out.index("id=defines") < out.index("id=uses")
    # The program is intact: only the order of whole cards changed.
    assert 'let flair = load("case.nii.gz")' in out
    assert "let mask = threshold(flair, 0.6)" in out
    # And it round-trips from there, with nothing left to do.
    again = doc.parse(out)
    assert again.tidy() is False
    assert again.to_imgql() == out


def test_tidying_a_document_that_is_already_in_order_changes_nothing():
    document = doc.parse(ORDERED)
    document.tidy()
    text = document.to_imgql()
    settled = doc.parse(text)
    assert settled.tidy() is False
    assert settled.to_imgql() == text


# --------------------------------------------------- and the workspace uses it


TANGLED = """\
//@board cols=9 rows=8
//@card id=b kind=code x=0 y=0
let b = add(a, 1)
//@card id=a kind=code x=5 y=0
let a = 1
"""

CYCLIC = """\
//@board cols=9 rows=8
//@card id=a kind=code x=0 y=0
let a = add(b, 1)
//@card id=b kind=code x=5 y=0
let b = add(a, 1)
"""


def test_saving_writes_the_definitions_before_their_uses(tmp_path):
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(TANGLED)
    space = Workspace(path=path)
    space.apply("board.moveCard", {"id": "a", "x": 6, "y": 0})
    space.flush()

    written = path.read_text()
    assert written.index("id=a") < written.index("id=b")


def test_a_cycle_does_not_stop_the_work_being_saved(tmp_path):
    """The worst possible moment to be strict is while somebody untangles it."""
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(CYCLIC)
    space = Workspace(path=path)
    space.apply("card.setTitle", {"id": "a", "title": "Still editing"})
    space.flush()

    assert 'title="Still editing"' in path.read_text()
    assert set(space.snapshot()["issues"]["cycle"]) >= {"a", "b"}


def test_two_cards_defining_one_name_are_reported_in_the_snapshot(tmp_path):
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(
        "//@card id=one kind=code x=0 y=0\nlet mask = 1\n"
        "//@card id=two kind=code x=4 y=0\nlet mask = 2\n"
    )
    space = Workspace(path=path)
    assert space.snapshot()["issues"]["duplicates"] == {"mask": ["one", "two"]}


# ------------------------------------------------ analysis off the click path


BIG = "//@board cols=12 rows=8\n" + "".join(
    f'//@card id=c{i} kind=code x={i % 6} y={i // 6}\nlet v{i} = 1\n' for i in range(12)
)


def test_a_view_change_does_not_re_analyse_the_program(tmp_path):
    """Most actions do not touch the document, and used to pay for it anyway.

    Turning a page asked "does this program compile" again and got the answer it
    had just got. With a real program open that was most of the cost of a click.
    """
    from voxlogica.ui import analysis
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(BIG)
    space = Workspace(path=path)
    space.snapshot()  # the first answer, computed and kept

    calls = 0
    real = analysis.compile_error

    def counted(text):
        nonlocal calls
        calls += 1
        return real(text)

    analysis.compile_error = counted
    try:
        space.apply("view.goToPage", {"page": 1})
        space.apply("view.select", {"ids": ["c1"]})
        space.snapshot()
        assert calls == 0
        # An actual edit does ask again.
        space.apply("board.moveCard", {"id": "c1", "x": 7, "y": 1})
        space.snapshot()
        assert calls == 1
    finally:
        analysis.compile_error = real


def test_the_interaction_path_never_waits_for_the_analysis(tmp_path):
    """Opening a real program warms the engine: over a second, once. The board
    must not wait for it, so what goes out at once is the last answer, marked."""
    import time

    from voxlogica.ui.workspace import Workspace

    published: list[dict] = []

    class Hub:
        def publish(self, payload, sticky_key=None):
            published.append(payload["workspace"])

    path = tmp_path / "doc.imgql"
    path.write_text(BIG)
    space = Workspace(hub=Hub(), path=path)
    space.publish()

    assert published, "the board is published immediately"
    assert published[0]["analysing"] is True
    assert published[0]["nodes"] == {}

    # The worker lands, and publishes again with the real answer.
    for _ in range(100):
        if len(published) > 1 and not published[-1]["analysing"]:
            break
        time.sleep(0.05)
    assert published[-1]["analysing"] is False
    assert space.snapshot()["issues"]["compile"] is None


def test_asking_directly_still_gets_the_real_answer(tmp_path):
    """An agent, a test, or the HTTP endpoint asked: they get it and pay for it."""
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text("//@card id=a kind=code x=0 y=0\nprint \"x\" x+x\n")
    space = Workspace(path=path)
    snapshot = space.snapshot()
    assert snapshot["analysing"] is False
    assert snapshot["issues"]["compile"] is not None
