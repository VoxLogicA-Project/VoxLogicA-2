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


def test_the_examples_are_found_in_the_checkout():
    found = library.examples_root()
    assert found is not None, "the shipped examples are not being found"
    assert found == ROOT / "examples"


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
    assert any(path.name == "sample.imgql" for path in files), (
        "the BraTS example is not in the list; only the top level is being read"
    )


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
