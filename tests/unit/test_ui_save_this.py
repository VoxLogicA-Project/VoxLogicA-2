"""Declaring what a card is about as an output of the program.

A card shows you a value; it cannot put one on disk. `print` and `save` are what
a program *says* it produces, so "save this" writes the directive into the text
rather than writing a file: a diff shows it, a colleague reads it, and a headless
run performs it. A button that wrote a file directly would be an effect with no
record of itself.
"""

from __future__ import annotations

import pytest

from voxlogica.ui import document as doc
from voxlogica.ui.workspace import Workspace

PROGRAM = """\
//@board cols=12 rows=8
//@card id=c1 kind=code x=0 y=0
let step = 1
let mask = step + 1
"""


def workspace_on(tmp_path, text=PROGRAM) -> Workspace:
    path = tmp_path / "doc.imgql"
    path.write_text(text)
    return Workspace(path=path)


# ------------------------------------------------------------- the document


def test_a_save_becomes_a_directive_and_a_card():
    document = doc.parse(PROGRAM)
    identity = document.add_output("save", "mask.nii", "mask")
    written = document.to_imgql()

    assert f'save "mask.nii" mask' in written
    assert f"//@card id={identity}" in written
    card = next(c for c in document.cards if c["id"] == identity)
    assert card["kind"] == "save"
    assert card["node"] == "mask"


def test_a_print_is_the_same_act_with_no_effect():
    document = doc.parse(PROGRAM)
    document.add_output("print", "mask", "mask")
    assert 'print "mask" mask' in document.to_imgql()


def test_what_it_writes_is_a_program_that_parses():
    """The invariant this whole format exists for: a document is always a valid
    .imgql program, including the moment after a button was pressed."""
    from voxlogica.ui import analysis

    document = doc.parse(PROGRAM)
    document.add_output("save", "out.nii", "mask")
    outputs = analysis.outputs(document.to_imgql())
    assert [(o.operation, o.label, o.binding) for o in outputs] == [
        ("save", "out.nii", "mask")
    ]


def test_a_label_with_quotes_survives_being_written():
    document = doc.parse(PROGRAM)
    document.add_output("save", 'he said "no"', "mask")
    written = document.to_imgql()
    assert r"\"no\"" in written
    # And it parses back to the label it started as.
    assert doc.parse(written).cards[-1]["title"] == 'he said "no"'


def test_nothing_is_written_for_a_card_about_nothing():
    document = doc.parse(PROGRAM)
    assert document.add_output("save", "x", "") is None


def test_only_print_and_save_are_outputs():
    document = doc.parse(PROGRAM)
    with pytest.raises(ValueError):
        document.add_output("compute", "x", "mask")


# --------------------------------------------------------------- the actions


def test_save_this_uses_what_the_card_is_about(tmp_path):
    """Its focus, which is the last binding it declares -- so pressing it on a
    fragment saves the answer, not the working."""
    workspace = workspace_on(tmp_path)
    identity = workspace.apply("card.saveThis", {"id": "c1"})
    assert identity
    assert 'save "mask" mask' in workspace.document.to_imgql()


def test_save_this_takes_a_label_when_it_is_given_one(tmp_path):
    workspace = workspace_on(tmp_path)
    workspace.apply("card.saveThis", {"id": "c1", "label": "run-07.nii"})
    assert 'save "run-07.nii" mask' in workspace.document.to_imgql()


def test_print_this_is_the_same_road(tmp_path):
    workspace = workspace_on(tmp_path)
    workspace.apply("card.printThis", {"id": "c1", "label": "answer"})
    assert 'print "answer" mask' in workspace.document.to_imgql()


def test_a_card_about_nothing_is_refused_rather_than_guessed_at(tmp_path):
    workspace = workspace_on(
        tmp_path, "//@board cols=9 rows=8\n//@card id=n1 kind=note x=0 y=0\n// hello\n"
    )
    with pytest.raises(ValueError, match="not about anything"):
        workspace.apply("card.saveThis", {"id": "n1"})


def test_an_unknown_card_is_refused(tmp_path):
    workspace = workspace_on(tmp_path)
    with pytest.raises(ValueError, match="no card"):
        workspace.apply("card.saveThis", {"id": "nope"})


def test_it_is_an_edit_and_so_it_can_be_undone(tmp_path):
    """It changes the program, which is exactly why it belongs in undo -- and
    exactly why it is not a button that quietly wrote a file."""
    workspace = workspace_on(tmp_path)
    before = workspace.document.to_imgql()
    workspace.apply("card.saveThis", {"id": "c1"})
    assert workspace.document.to_imgql() != before
    workspace.apply("workspace.undo", {})
    assert workspace.document.to_imgql() == before
