"""Labels, and the one property that makes them worth having.

A label is written into the file that carries it, so it survives every way a
file can move that this UI never saw: a `git mv`, a copy, a restore from backup,
a colleague's mail. An index would be faster and would detach the first time
somebody moved a file in a terminal -- silently, which is the part that costs an
afternoon. These tests are mostly about that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.ui import labels
from voxlogica.ui.document import parse
from voxlogica.ui.workspace import Workspace

PLAIN = "let a = 1\n"
LABELLED = '//@board cols=12 rows=8 labels="draft,wt"\n//@card id=c1 kind=code x=0 y=0\nlet a = 1\n'


# ------------------------------------------------------------------- reading


def test_labels_are_read_from_the_board_line():
    assert labels.parse(LABELLED) == ["draft", "wt"]


def test_a_file_with_no_labels_has_none():
    assert labels.parse(PLAIN) == []
    assert labels.parse("//@board cols=12 rows=8\n") == []


def test_a_hand_written_unquoted_list_is_still_read():
    """Written quoted always, the way every other prose field here is. Read
    either way, because a file somebody edited by hand should not lose its
    labels for having been written the obvious way."""
    assert labels.parse("//@board cols=2 labels=bare,two\n") == ["bare", "two"]


def test_the_word_labels_elsewhere_in_the_file_is_not_a_label():
    assert labels.parse('let labels = "draft"\n') == []


def test_a_label_cannot_contain_the_separator_or_the_delimiter():
    assert labels.clean(' dr"aft, ') == "draft"
    assert labels.clean("   ") == ""


# ------------------------------------------------------------------- writing


def test_a_label_round_trips_through_the_file():
    document = parse(LABELLED)
    document.set_board(labels="draft,wt,done")
    written = document.to_imgql()
    assert 'labels="draft,wt,done"' in written
    assert labels.parse(written) == ["draft", "wt", "done"]


def test_labelling_a_plain_program_gives_it_directives():
    """An edit like any other, and the format's whole promise is that adding a
    comment leaves it a valid program."""
    document = parse(PLAIN)
    document.set_board(labels="draft")
    written = document.to_imgql()
    assert labels.parse(written) == ["draft"]
    assert "let a = 1" in written


def test_labels_are_written_quoted_always():
    document = parse(PLAIN)
    document.set_board(labels="one")
    assert 'labels="one"' in document.to_imgql()


# ------------------------------------------------------------ through actions


@pytest.fixture()
def workspace(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PLAIN)
    return Workspace(path=path), path


def test_adding_and_removing_a_label(workspace):
    space, path = workspace
    assert space.apply("library.addLabel", {"path": str(path), "label": "draft"}) is True
    assert labels.parse(space.document.to_imgql()) == ["draft"]

    assert space.apply("library.removeLabel", {"path": str(path), "label": "draft"}) is True
    assert labels.parse(space.document.to_imgql()) == []


def test_the_same_label_twice_is_one_label(workspace):
    space, path = workspace
    space.apply("library.addLabel", {"path": str(path), "label": "draft"})
    assert space.apply("library.addLabel", {"path": str(path), "label": "draft"}) is False
    assert labels.parse(space.document.to_imgql()) == ["draft"]


def test_removing_one_that_is_not_there_changes_nothing(workspace):
    space, path = workspace
    assert space.apply("library.removeLabel", {"path": str(path), "label": "nope"}) is False


def test_a_label_that_is_only_punctuation_is_refused(workspace):
    space, path = workspace
    assert space.apply("library.addLabel", {"path": str(path), "label": ' ," '}) is False


def test_order_is_the_order_they_were_given(workspace):
    """A list that reshuffled itself would put noise in a diff nobody asked
    for."""
    space, path = workspace
    for label in ("wt", "draft", "brats"):
        space.apply("library.addLabel", {"path": str(path), "label": label})
    assert labels.parse(space.document.to_imgql()) == ["wt", "draft", "brats"]


def test_labelling_a_file_that_is_not_open_edits_that_file(tmp_path):
    """Labelling something is not opening it."""
    open_path = tmp_path / "open.imgql"
    open_path.write_text(PLAIN)
    other = tmp_path / "other.imgql"
    other.write_text("let b = 2\n")

    space = Workspace(path=open_path)
    assert space.apply("library.addLabel", {"path": str(other), "label": "later"}) is True

    assert labels.parse(other.read_text()) == ["later"]
    # And the open document is untouched.
    assert labels.parse(space.document.to_imgql()) == []


# -------------------------------------------------------------- reading files


def test_labels_of_a_file_are_read_from_its_head(tmp_path):
    path = tmp_path / "x.imgql"
    path.write_text(LABELLED)
    assert labels.of(path) == ["draft", "wt"]


def test_a_file_that_is_not_there_has_no_labels(tmp_path):
    assert labels.of(tmp_path / "missing.imgql") == []


def test_a_changed_file_is_read_again(tmp_path):
    """The cache is keyed on mtime and size; a stale answer here would show a
    label somebody just removed."""
    path = tmp_path / "x.imgql"
    path.write_text(LABELLED)
    assert labels.of(path) == ["draft", "wt"]

    path.write_text('//@board cols=12 rows=8 labels="other"\nlet a = 1\n')
    assert labels.of(path) == ["other"]


def test_deleting_a_file_leaves_nothing_behind(tmp_path):
    """The property an index would fail: there is nowhere else for a label to
    be, so there is nothing to clean up."""
    path = tmp_path / "x.imgql"
    path.write_text(LABELLED)
    assert labels.of(path) == ["draft", "wt"]

    path.unlink()
    labels.forget(path)
    assert labels.of(path) == []


def test_a_moved_file_keeps_its_labels(tmp_path):
    """The whole reason this is not an index: `git mv` is not an exotic
    failure, it is Tuesday."""
    first = tmp_path / "before.imgql"
    first.write_text(LABELLED)
    second = tmp_path / "after.imgql"
    first.rename(second)
    assert labels.of(second) == ["draft", "wt"]


# ---------------------------------------------------------------- filtering


def test_the_filter_matches_a_prefix_of_a_label():
    assert labels.matches(["draft", "wt"], "label:dr") is True
    assert labels.matches(["draft"], "label:zz") is False


def test_an_unknown_label_selects_nothing():
    assert labels.matches(["draft"], "label:nothinglikethis") is False


def test_an_empty_term_selects_everything():
    assert labels.matches([], "label:") is True
