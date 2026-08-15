"""The library is the filesystem, and the sidebar is the tab bar.

There is no index and no manifest here on purpose, so these tests are written
against real directories: anything they assert about the model has to be true of
the folders themselves, which is the property that makes "put it in git" work.
"""

from __future__ import annotations

import shutil

import pytest

from voxlogica.ui import home, library
from voxlogica.ui.workspace import Workspace


@pytest.fixture(autouse=True)
def _library(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path / "appdata"))
    library.root().mkdir(parents=True)
    return library.root()


def test_a_folder_is_a_project_and_an_imgql_in_it_is_a_file():
    (library.root() / "loose.imgql").write_text("let a = 1\n")
    (library.root() / "Segmentation").mkdir()
    (library.root() / "Segmentation" / "brats.imgql").write_text("let b = 2\n")

    tree = library.tree()
    assert [p["name"] for p in tree["projects"]] == ["Segmentation"]
    assert [(f["name"], f["project"]) for f in tree["files"]] == [
        ("loose", None),
        ("brats", "Segmentation"),
    ]


def test_an_empty_project_is_still_a_project():
    """Somebody made it because they are about to put something in it."""
    library.new_project("Later")
    assert [p["name"] for p in library.projects()] == ["Later"]
    assert library.tree()["files"] == []


def test_nothing_that_is_not_ours_is_listed():
    (library.root() / ".git").mkdir()
    (library.root() / "node_modules").mkdir()
    (library.root() / "notes.txt").write_text("not a workspace")
    assert library.projects() == []
    assert library.tree()["files"] == []


def test_a_new_file_lands_where_it_was_asked_for():
    loose = library.new_file()
    filed = library.new_file(project="Study")
    assert loose.parent == library.root()
    assert filed.parent == library.root() / "Study"
    assert loose.exists() and filed.exists()


def test_two_files_made_in_the_same_second_do_not_collide():
    first = library.new_file(name="same")
    second = library.new_file(name="same")
    assert first != second
    assert second.stem == "same-2"


def test_a_name_that_would_change_which_folder_this_is_is_refused():
    """Only the characters that would mean something else are touched. A space
    or an accent is somebody's language and none of our business."""
    made = library.new_file(name="../../etc/passwd")
    assert made.parent == library.root()
    assert "/" not in made.name

    spaced = library.new_file(name="Ictus paziente 3")
    assert spaced.name == "Ictus paziente 3.imgql"


def test_moving_a_file_into_a_project_moves_the_file():
    made = library.new_file(name="drifting")
    moved = library.move(made, "Somewhere")
    assert moved.parent == library.root() / "Somewhere"
    assert not made.exists()
    assert moved.exists()


def test_moving_a_file_out_puts_it_back_at_the_top():
    made = library.new_file(project="Somewhere", name="filed")
    moved = library.move(made, None)
    assert moved.parent == library.root()


def test_a_move_that_would_overwrite_something_does_not():
    library.new_file(project="P", name="taken")
    other = library.new_file(name="taken")
    moved = library.move(other, "P")
    assert moved.name == "taken-2.imgql"
    assert (library.root() / "P" / "taken.imgql").read_text() == ""


def test_renaming_a_file_keeps_the_suffix():
    made = library.new_file(name="before")
    renamed = library.rename(made, "after")
    assert renamed.name == "after.imgql"
    assert not made.exists()


def test_renaming_onto_an_existing_name_is_refused():
    library.new_file(name="taken")
    other = library.new_file(name="other")
    with pytest.raises(FileExistsError):
        library.rename(other, "taken")


def test_deleting_a_file_leaves_its_project_alone():
    """A project that has just lost its last file is still a project."""
    made = library.new_file(project="Keep", name="only")
    assert library.delete(made) is True
    assert [p["name"] for p in library.projects()] == ["Keep"]


# ------------------------------------------- the open file follows what happens


def test_the_open_file_follows_a_move():
    made = library.new_file(name="wandering")
    space = Workspace(path=made)
    moved = library.move(made, "Elsewhere")
    assert space.follow(made, moved) is True
    assert space.path == moved
    assert space.snapshot()["path"] == str(moved)


def test_a_file_that_is_not_open_does_not_drag_the_open_one_along():
    open_file = library.new_file(name="open")
    other = library.new_file(name="other")
    space = Workspace(path=open_file)
    assert space.follow(other, library.move(other, "P")) is False
    assert space.path == open_file


def test_the_open_file_follows_its_project_being_renamed():
    made = library.new_file(project="Old", name="doc")
    space = Workspace(path=made)
    after = library.rename_project("Old", "New")
    assert space.follow_folder(library.root() / "Old", library.root() / after) is True
    assert space.path == library.root() / "New" / "doc.imgql"


def test_deleting_the_open_file_leaves_nothing_open():
    made = library.new_file(name="doomed")
    space = Workspace(path=made)
    library.delete(made)
    assert space.forget(made) is True
    assert space.path is None
    assert space.snapshot()["cards"]  # the empty document is still a document


def test_opening_another_file_writes_what_the_last_one_owed(tmp_path):
    """An unwritten change must not survive as a change to a different file."""
    first = library.new_file(name="first")
    second = library.new_file(name="second")
    space = Workspace(path=first)
    space.apply("card.setTitle", {"id": "program", "title": "Belongs to the first"})

    space.apply("library.open", {"path": str(second)})

    assert 'title="Belongs to the first"' in first.read_text()
    assert space.path == second


def test_the_library_arrives_in_the_snapshot():
    made = library.new_file(project="Visible", name="here")
    space = Workspace(path=made)
    tree = space.snapshot()["library"]
    assert [p["name"] for p in tree["projects"]] == ["Visible"]
    assert [(f["name"], f["open"]) for f in tree["files"]] == [("here", True)]


# ------------------------------------------------- folders from somewhere else


def test_an_existing_folder_can_be_shown_as_a_project(tmp_path):
    """Nothing is moved or copied: a repository somebody already has appears
    here, and its files are still exactly its files."""
    elsewhere = tmp_path / "repos" / "brats"
    elsewhere.mkdir(parents=True)
    (elsewhere / "study.imgql").write_text("let a = 1\n")

    assert library.link(elsewhere) == "brats"
    tree = library.tree()
    assert [(p["name"], p["linked"]) for p in tree["projects"]] == [("brats", True)]
    assert [(f["name"], f["project"]) for f in tree["files"]] == [("study", "brats")]
    # Still where it was.
    assert (elsewhere / "study.imgql").exists()


def test_a_file_made_in_a_linked_project_lands_in_that_folder(tmp_path):
    elsewhere = tmp_path / "repos" / "brats"
    elsewhere.mkdir(parents=True)
    library.link(elsewhere)
    made = library.new_file(project="brats", name="new")
    assert made.parent == elsewhere.resolve()


def test_unlinking_a_folder_leaves_the_folder_alone(tmp_path):
    elsewhere = tmp_path / "repos" / "brats"
    elsewhere.mkdir(parents=True)
    (elsewhere / "study.imgql").write_text("let a = 1\n")
    library.link(elsewhere)
    assert library.unlink(elsewhere) is True
    assert library.projects() == []
    assert (elsewhere / "study.imgql").exists()


def test_a_linked_folder_that_has_gone_is_simply_not_there(tmp_path):
    elsewhere = tmp_path / "repos" / "gone"
    elsewhere.mkdir(parents=True)
    library.link(elsewhere)
    shutil.rmtree(elsewhere)
    # No error, no ghost row: the filesystem is still the truth.
    assert library.projects() == []


def test_linking_a_folder_that_is_already_a_project_does_not_double_it():
    """Two entries for one directory means its files are listed twice."""
    inside = library.root() / "Segmentation"
    inside.mkdir()
    (inside / "study.imgql").write_text("let a = 1\n")

    library.link(inside)

    tree = library.tree()
    assert [p["name"] for p in tree["projects"]] == ["Segmentation"]
    assert [f["name"] for f in tree["files"]] == ["study"]


def test_linking_the_same_folder_twice_by_different_paths_does_not_double_it(tmp_path):
    elsewhere = tmp_path / "repos" / "brats"
    elsewhere.mkdir(parents=True)
    (elsewhere / "study.imgql").write_text("let a = 1\n")

    library.link(elsewhere)
    library.link(tmp_path / "repos" / "." / "brats")

    assert [p["name"] for p in library.projects()] == ["brats"]
    assert [f["name"] for f in library.tree()["files"]] == ["study"]


def test_an_empty_project_can_be_tidied_away():
    library.new_project("Never used")
    assert library.delete_project("Never used") is True
    assert library.projects() == []


def test_a_project_with_files_in_it_is_not_deleted():
    """Deleting it would be deleting the files, which is a different act."""
    library.new_file(project="In use", name="something")
    assert library.delete_project("In use") is False
    assert [p["name"] for p in library.projects()] == ["In use"]


def test_a_linked_folder_is_not_ours_to_delete(tmp_path):
    elsewhere = tmp_path / "repos" / "brats"
    elsewhere.mkdir(parents=True)
    library.link(elsewhere)
    assert library.delete_project("brats") is False
    assert elsewhere.exists()


def test_copying_a_file_leaves_the_original_where_it_was():
    made = library.new_file(name="original")
    made.write_text("let a = 1\n")
    copy = library.copy(made, "Elsewhere")
    assert copy.parent == library.root() / "Elsewhere"
    assert copy.read_text() == "let a = 1\n"
    assert made.exists()


def test_copying_into_the_same_place_does_not_overwrite():
    made = library.new_file(name="twice")
    made.write_text("let a = 1\n")
    copy = library.copy(made, None)
    assert copy.name == "twice-2.imgql"
    assert made.read_text() == "let a = 1\n"
