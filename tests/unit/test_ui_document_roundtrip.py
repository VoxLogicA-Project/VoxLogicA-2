"""The workspace document round-trips .imgql without losing a byte.

The strict requirement in doc/dev/ui-workspace.md section 6 is losslessness, so
these tests are written as equalities on bytes rather than on structure: a test
that compared parsed shapes would pass while the file was being quietly
reformatted underneath it.
"""

from pathlib import Path

import pytest

from voxlogica.ui import document as doc

REPO = Path(__file__).resolve().parents[2]

ANNOTATED = """\
//@board cols=9 rows=8
//@card id=segmentation kind=code x=0 y=0 w=5 h=4
let flair = load("case.nii.gz")
let mask  = threshold(flair, 0.6)

//@card id=dice kind=result x=5 y=0 w=4 h=3 node=mask view=state
//@card id=todo kind=note x=0 y=4 w=5 h=2
// sweep the threshold before trusting this
"""

PLAIN = """\
// an ordinary program, never opened in the UI
let a = load("x.nii.gz")
print "a" a
"""


def test_an_untouched_annotated_file_exports_as_the_bytes_it_came_in_as():
    assert doc.parse(ANNOTATED).to_imgql() == ANNOTATED


def test_an_untouched_plain_file_exports_as_the_bytes_it_came_in_as():
    # Opening a program must never annotate somebody's source behind their back.
    assert doc.parse(PLAIN).to_imgql() == PLAIN


@pytest.mark.parametrize(
    "path",
    sorted(REPO.glob("implementation/python/tests/*.imgql"))[:5]
    or sorted(REPO.glob("**/*.imgql"))[:5],
    ids=lambda path: path.name,
)
def test_real_programs_survive_a_round_trip_unchanged(path):
    text = path.read_text()
    assert doc.parse(text).to_imgql() == text


def test_a_file_without_directives_is_one_code_card_holding_all_of_it():
    cards = doc.parse(PLAIN).cards
    assert len(cards) == 1
    assert cards[0]["kind"] == "code"
    assert cards[0]["source"] == PLAIN
    # Never sized by hand, so the board is free to size it to its content.
    assert cards[0]["auto"] is True


def test_cards_carry_geometry_and_kind_specific_attributes():
    cards = {card["id"]: card for card in doc.parse(ANNOTATED).cards}
    assert (cards["segmentation"]["x"], cards["segmentation"]["w"]) == (0, 5)
    assert cards["segmentation"]["source"].startswith('let flair = load("case.nii.gz")')
    assert cards["dice"]["node"] == "mask"
    assert cards["dice"]["view"] == "state"
    assert cards["segmentation"]["auto"] is False


def test_moving_a_card_rewrites_that_line_and_nothing_else():
    document = doc.parse(ANNOTATED)
    assert document.place("dice", x=2, y=5)
    out = document.to_imgql()

    before = ANNOTATED.splitlines()
    after = out.splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after)
    assert changed == [5]
    assert "x=2 y=5" in after[5]
    # The other card's body is untouched, byte for byte.
    assert 'let flair = load("case.nii.gz")' in out


def test_an_attribute_this_build_does_not_understand_survives():
    text = "//@card id=a kind=code x=0 y=0 futureThing=42\nlet a = 1\n"
    document = doc.parse(text)
    document.place("a", x=3)
    assert "futureThing=42" in document.to_imgql()


def test_arranging_a_plain_file_annotates_it_but_keeps_the_program():
    document = doc.parse(PLAIN)
    assert document.place("program", x=1, y=2, w=4, h=3)
    out = document.to_imgql()
    assert out.endswith(PLAIN)
    assert out.startswith("//@board ")
    assert '//@card id=program title="Program" kind=code x=1 y=2 w=4 h=3' in out
    # And it still round-trips from there.
    assert doc.parse(out).to_imgql() == out


def test_editing_one_card_moves_only_that_card_s_text():
    document = doc.parse(ANNOTATED)
    assert document.set_source("segmentation", "let flair = 1\n")
    out = document.to_imgql()
    assert "let flair = 1" in out
    assert "let mask" not in out
    assert "//@card id=todo kind=note x=0 y=4 w=5 h=2" in out
    assert "// sweep the threshold before trusting this" in out


NOTES = """\
//@board cols=6 rows=4
//@card id=n kind=note x=0 y=0 w=3 h=2
// prose, stored as a comment so the file still runs
//@card id=c kind=code x=3 y=0 w=3 h=2
let a = 1
"""


def test_a_note_reaches_the_ui_without_the_comment_prefix():
    cards = {card["id"]: card for card in doc.parse(NOTES).cards}
    assert cards["n"]["source"] == "prose, stored as a comment so the file still runs\n"
    # Code is handed over exactly as written; only prose is un-commented.
    assert cards["c"]["source"] == "let a = 1\n"


def test_editing_a_note_keeps_the_document_a_runnable_program():
    document = doc.parse(NOTES)
    document.set_source("n", "a sentence\nand another\n")
    out = document.to_imgql()
    assert "// a sentence\n// and another\n" in out
    # Nothing outside a comment except the program itself.
    code = [line for line in out.splitlines() if line and not line.lstrip().startswith("//")]
    assert code == ["let a = 1"]


def test_a_note_survives_the_round_trip_through_the_ui():
    document = doc.parse(NOTES)
    text = next(card for card in document.cards if card["id"] == "n")["source"]
    document.set_source("n", text)
    assert doc.parse(document.to_imgql()).cards[0]["source"] == text


QUOTED = '''\
//@board cols=8 rows=6
//@card id=c1 title="A card with \\"quotes\\" and spaces" kind=code x=0 y=0 w=4 h=2
let a = 1
'''


def test_a_title_survives_quotes_and_spaces():
    card = doc.parse(QUOTED).cards[0]
    assert card["title"] == 'A card with "quotes" and spaces'
    assert card["id"] == "c1"


def test_a_title_is_written_back_quoted_and_escaped():
    document = doc.parse(QUOTED)
    document.set_attr("c1", "title", 'say "hello" \\ goodbye')
    out = document.to_imgql()
    assert r'title="say \"hello\" \\ goodbye"' in out
    # And reading it back gives the same string, which is the only test that
    # matters: the escaping is a detail, the round trip is the contract.
    assert doc.parse(out).cards[0]["title"] == 'say "hello" \\ goodbye'


def test_a_title_is_always_quoted_even_when_it_need_not_be():
    document = doc.parse(QUOTED)
    document.set_attr("c1", "title", "plain")
    assert 'title="plain"' in document.to_imgql()


def test_an_old_file_reads_its_id_as_its_name():
    """Files written before id and title were separate have only an id.

    It reads as a name because that is what it was; the card keeps working, and
    keeps its reference.
    """
    card = doc.parse(ANNOTATED).cards[0]
    assert card["title"] == card["id"]


def test_two_cards_may_share_a_title_but_not_an_id():
    """A title is a name, and names collide. Identity is what must not."""
    document = doc.parse(QUOTED)
    document.set_attr("c1", "title", "threshold")
    assert document.add_card(document.next_id(), "note", title="threshold")
    titles = [card["title"] for card in document.cards]
    ids = [card["id"] for card in document.cards]
    assert titles == ["threshold", "threshold"]
    assert len(set(ids)) == 2


def test_the_only_card_of_a_plain_file_can_be_renamed():
    """A file with no directives is shown as one card, and that card is real.

    Renaming it is the change that turns the plain file into an annotated one.
    It used to do nothing at all: the rename went to a card the document did
    not think it had.
    """
    document = doc.parse(PLAIN)
    assert document.set_attr("program", "title", "My analysis") is True
    out = document.to_imgql()
    assert 'title="My analysis"' in out
    assert out.endswith(PLAIN)
    assert doc.parse(out).cards[0]["title"] == "My analysis"
