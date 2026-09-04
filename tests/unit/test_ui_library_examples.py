"""The programs that ship with VoxLogicA are a project, without being mounted.

The first question a new workspace raises is "what does one of these look
like", and the answer is on disk already. Making somebody find the checkout in
a folder picker to see it is the answer being there and not being offered.

It is an ordinary folder of ordinary files in every way but two: it appears
without being added, and it is not theirs to lose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.ui import library

ROOT = Path(__file__).resolve().parents[2]


def test_the_examples_are_the_gallery_and_not_a_second_collection():
    """`doc/gallery` is the maintained one: a reading path through the language,
    ordered in its own README. Pointing the sidebar at anything else would be
    inventing a second collection beside it, which is how the first version of
    this shipped -- one folder, one example, next to a gallery of twenty."""
    found = library.examples_root()
    assert found is not None, "the shipped examples are not being found"
    assert found == ROOT / "doc" / "gallery" / "programs"


def test_the_whole_gallery_is_there():
    listed = {entry.path.stem for entry in library.scan() if entry.project == library.EXAMPLES}
    on_disk = {path.stem for path in (ROOT / "doc" / "gallery" / "programs").rglob("*.imgql")}
    assert listed == on_disk
    assert len(listed) > 5, "the gallery is barely there; is the scan finding subfolders?"


def test_they_appear_as_a_project_nobody_added():
    names = [project["name"] for project in library.projects()]
    assert library.EXAMPLES in names


def test_the_project_says_it_is_ours():
    """So the sidebar can offer a different menu on it: there is no Forget, and
    no Delete, for a project the user never made."""
    shipped = next(p for p in library.projects() if p["name"] == library.EXAMPLES)
    assert shipped["builtin"] is True
    assert all(
        p["builtin"] is False for p in library.projects() if p["name"] != library.EXAMPLES
    ), "an ordinary project is being marked as ours"


def test_an_example_in_a_subfolder_is_still_listed():
    """An example is a program *and its data*, so each lives in its own folder.
    A flat listing would show an empty project -- which is how this was found."""
    files = [entry.path for entry in library.scan() if entry.project == library.EXAMPLES]
    assert any(path.name == "brats-five-cases.imgql" for path in files), (
        "the BraTS example is not in the list; only the top level is being read"
    )
    assert any(path.name == "intro-hello.imgql" for path in files)


def test_looking_deeper_is_confined_to_what_we_ship(tmp_path, monkeypatch):
    """Nobody's own project starts behaving differently from the folder they can
    see in Finder."""
    nested = tmp_path / "theirs" / "inner"
    nested.mkdir(parents=True)
    (nested / "deep.imgql").write_text("let a = 1\n")
    monkeypatch.setattr(library, "links", lambda: [tmp_path / "theirs"])

    found = [entry.path.name for entry in library.scan() if entry.project == "theirs"]
    assert "deep.imgql" not in found


def test_a_new_file_is_refused_rather_than_written_into_the_examples():
    """A new file goes to the selected project, and the selected project can be
    this one. Writing into the program's own source tree is not what anybody
    pressing the plus meant."""
    with pytest.raises(library.ReadOnlyProject):
        library.new_file(project=library.EXAMPLES)


def test_it_cannot_be_forgotten_because_it_was_never_linked():
    """Forget removes a link. This is not a link -- it is where the program
    lives -- so there is nothing there to remove and nothing to go wrong."""
    shipped = library.examples_root()
    assert shipped is not None
    assert shipped.resolve() not in {folder.resolve() for folder in library.links()}


# ------------------------------------------------------------- and read-only


def test_an_example_is_never_written_to(tmp_path, monkeypatch):
    """The gap the project-level guard left, and the one that actually bit.

    There is no Save here -- the file *is* the document, written shortly after
    you stop -- so opening an example and nudging one card put a layout comment
    into the checkout. It was found as a stray `//@card` in a shipped example,
    committed by accident.
    """
    from voxlogica.ui.workspace import Workspace

    shipped = library.examples_root()
    assert shipped is not None
    example = next(shipped.rglob("*.imgql"))
    before = example.read_text()

    workspace = Workspace(path=example)
    assert workspace.read_only is True
    workspace.document.place(workspace.document.cards[0]["id"], page=3)
    workspace.flush()
    assert example.read_text() == before, "an example was written to"


def test_the_snapshot_says_so(tmp_path):
    """Refusing in silence would be the worst of both: the edit appears to take
    and is gone the next time the file is opened."""
    from voxlogica.ui.workspace import Workspace

    shipped = library.examples_root()
    assert shipped is not None
    assert Workspace(path=next(shipped.rglob("*.imgql"))).snapshot()["readOnly"] is True

    ordinary = tmp_path / "mine.imgql"
    ordinary.write_text("let a = 1\n")
    assert Workspace(path=ordinary).snapshot()["readOnly"] is False


def test_saving_it_somewhere_else_is_how_you_start_editing_one(tmp_path):
    """Save As is not blocked -- it is the answer. What is blocked is putting it
    back where it came from."""
    from voxlogica.ui.workspace import Workspace

    shipped = library.examples_root()
    assert shipped is not None
    workspace = Workspace(path=next(shipped.rglob("*.imgql")))

    mine = tmp_path / "mine.imgql"
    assert workspace.save(str(mine)) == str(mine)
    assert mine.exists()
    assert workspace.read_only is False, "the copy is mine to edit"

    with pytest.raises(PermissionError):
        workspace.save(str(next(shipped.rglob("*.imgql"))))
