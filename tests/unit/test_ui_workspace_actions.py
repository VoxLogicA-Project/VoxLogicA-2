"""The action vocabulary is one vocabulary.

The whole point of the manifest is that the browser and an MCP agent can do the
same things. Two hand-written lists would drift on the first busy afternoon, so
these tests fail the moment they disagree -- once for the TypeScript facade, once
for the MCP tool list.
"""

import re
from pathlib import Path

import pytest

from voxlogica.ui import actions as action_module
from voxlogica.ui.workspace import Workspace

REPO = Path(__file__).resolve().parents[2]
FACADE = REPO / "implementation/ui/src/lib/actions/index.ts"

PROGRAM = """\
//@board cols=9 rows=8
//@card id=a kind=code x=0 y=0 w=4 h=3
let a = 1
//@card id=b kind=result x=4 y=0 w=3 h=2 node=a view=state
"""


@pytest.fixture()
def workspace(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    return Workspace(path=path)


def test_every_action_exists_in_the_typescript_facade():
    source = FACADE.read_text()
    # `board.moveCard` in Python is `moveCard:` under `export const board` in TS.
    missing = []
    for name in action_module.ACTIONS:
        namespace, method = name.split(".")
        if not re.search(rf"\b{method}\s*:", source):
            missing.append(name)
    assert not missing, (
        f"the UI cannot call {missing}: every action in the manifest must appear "
        "in src/lib/actions/index.ts, or one side has grown a capability the "
        "other does not have"
    )


def test_the_facade_invents_no_action_the_server_does_not_have():
    source = FACADE.read_text()
    invoked = set(re.findall(r'invoke<[^>]*>\("([^"]+)"|invoke\("([^"]+)"', source))
    names = {first or second for first, second in invoked}
    unknown = names - set(action_module.ACTIONS)
    assert not unknown, f"the UI calls actions that do not exist: {sorted(unknown)}"


def test_every_action_becomes_an_mcp_tool_schema():
    for name, action in action_module.ACTIONS.items():
        schema = action_module.json_schema(action)
        assert schema["type"] == "object", name
        assert set(schema["required"]) <= set(schema["properties"]), name


def test_moving_a_card_changes_the_document_and_the_snapshot(workspace):
    assert workspace.apply("board.moveCard", {"id": "a", "x": 2, "y": 1}) is True
    card = next(card for card in workspace.snapshot()["cards"] if card["id"] == "a")
    assert (card["x"], card["y"]) == (2, 1)
    assert "//@card id=a kind=code x=2 y=1 w=4 h=3" in workspace.document.to_imgql()


def test_an_unknown_action_is_refused_by_name(workspace):
    with pytest.raises(KeyError):
        workspace.apply("board.teleportCard", {"id": "a"})


def test_a_missing_parameter_is_refused_before_anything_changes(workspace):
    before = workspace.document.to_imgql()
    with pytest.raises(ValueError):
        workspace.apply("board.moveCard", {"id": "a"})
    assert workspace.document.to_imgql() == before


def test_switching_a_card_s_mode_is_an_action(workspace):
    workspace.apply("card.setKind", {"id": "b", "kind": "note"})
    workspace.apply("card.setViewMode", {"id": "b", "view": "content"})
    card = next(card for card in workspace.snapshot()["cards"] if card["id"] == "b")
    assert card["kind"] == "note"
    assert card["view"] == "content"


def test_the_view_is_workspace_state_but_not_document_state(workspace):
    before = workspace.document.to_imgql()
    workspace.apply("view.goToPage", {"page": 3})
    assert workspace.snapshot()["view"]["page"] == 3
    # Which page somebody is looking at is not a property of the program.
    assert workspace.document.to_imgql() == before


def test_saving_writes_exactly_what_export_returns(workspace, tmp_path):
    workspace.apply("board.resizeCard", {"id": "a", "w": 6, "h": 2})
    target = tmp_path / "out.imgql"
    workspace.apply("workspace.save", {"path": str(target)})
    assert target.read_text() == workspace.apply("workspace.export", {})


def test_one_drag_reaches_the_document_as_one_change(workspace):
    """A card that displaces others is a single arrangement, not a burst of moves.

    Sent separately there is a moment when the document really does hold two
    cards on the same cells, and a save, a reload or an agent reading the
    workspace at that moment sees a broken layout.
    """
    assert workspace.apply("board.arrange", {"cards": [
        {"id": "a", "x": 4, "y": 0},
        {"id": "b", "x": 0, "y": 0},
    ]}) is True
    placed = {card["id"]: (card["x"], card["y"]) for card in workspace.snapshot()["cards"]}
    assert placed == {"a": (4, 0), "b": (0, 0)}


def test_an_arrangement_naming_an_unknown_card_moves_nothing(workspace):
    before = {card["id"]: (card["x"], card["y"]) for card in workspace.snapshot()["cards"]}
    assert workspace.apply("board.arrange", {"cards": [
        {"id": "a", "x": 7, "y": 7},
        {"id": "ghost", "x": 0, "y": 0},
    ]}) is False
    after = {card["id"]: (card["x"], card["y"]) for card in workspace.snapshot()["cards"]}
    assert after == before


def test_an_arrangement_can_move_and_resize_in_the_same_breath(workspace):
    assert workspace.apply("board.arrange", {"cards": [
        {"id": "a", "x": 1, "y": 1, "w": 2, "h": 2},
    ]}) is True
    assert "//@card id=a kind=code x=1 y=1 w=2 h=2" in workspace.document.to_imgql()


def test_undo_puts_the_document_back(workspace):
    before = workspace.document.to_imgql()
    workspace.apply("board.moveCard", {"id": "a", "x": 7, "y": 3})
    assert workspace.document.to_imgql() != before
    assert workspace.apply("workspace.undo") is True
    assert workspace.document.to_imgql() == before
    assert workspace.apply("workspace.redo") is True
    assert "x=7 y=3" in workspace.document.to_imgql()


def test_looking_at_something_is_not_an_edit(workspace):
    """Turning a page must not be undoable.

    An undo stack that made you step back through everything you had looked at
    is a stack nobody would use twice.
    """
    workspace.apply("view.goToPage", {"page": 3})
    workspace.apply("view.setZoom", {"zoom": 1.5})
    assert workspace.apply("workspace.undo") is False


def test_an_edit_after_an_undo_closes_the_branch(workspace):
    workspace.apply("board.moveCard", {"id": "a", "x": 7, "y": 3})
    workspace.apply("workspace.undo")
    workspace.apply("board.moveCard", {"id": "a", "x": 1, "y": 1})
    # Redoing now would restore a document that never existed.
    assert workspace.apply("workspace.redo") is False


def test_undo_is_empty_after_opening_another_document(workspace, tmp_path):
    workspace.apply("board.moveCard", {"id": "a", "x": 7, "y": 3})
    other = tmp_path / "other.imgql"
    other.write_text("let b = 2\n")
    workspace.apply("workspace.open", {"path": str(other)})
    assert workspace.apply("workspace.undo") is False


def test_a_duplicate_carries_the_card_s_contents(workspace):
    assert workspace.apply(
        "board.duplicateCard", {"id": "a", "newId": "a2", "x": 5, "y": 4}
    ) is True
    cards = {card["id"]: card for card in workspace.snapshot()["cards"]}
    assert cards["a2"]["source"] == cards["a"]["source"] == "let a = 1\n"
    assert (cards["a2"]["x"], cards["a2"]["y"]) == (5, 4)
    assert cards["a2"]["kind"] == "code"


def test_a_duplicate_will_not_take_a_name_that_exists(workspace):
    assert workspace.apply("board.duplicateCard", {"id": "a", "newId": "b"}) is False


def test_a_change_reaches_the_file_without_anybody_saving(workspace, tmp_path):
    """There is no save action to forget.

    A workspace is not a thing you save, any more than a drawer is: the file is
    the document, and an unsaved change is only a change nobody wrote down yet.
    """
    workspace.apply("board.moveCard", {"id": "a", "x": 6, "y": 2})
    workspace.flush()
    assert "//@card id=a kind=code x=6 y=2" in workspace.path.read_text()
    assert workspace.snapshot()["dirty"] is False


def test_a_burst_of_changes_costs_one_write(workspace):
    for x in range(5):
        workspace.apply("board.moveCard", {"id": "a", "x": x, "y": 0})
    # Still pending: the clock restarted on each one.
    assert workspace.snapshot()["dirty"] is True
    workspace.flush()
    assert "x=4 y=0" in workspace.path.read_text()


def test_looking_at_something_writes_nothing(workspace):
    workspace.flush()
    before = workspace.path.stat().st_mtime_ns
    workspace.apply("view.goToPage", {"page": 2})
    workspace.flush()
    assert workspace.path.stat().st_mtime_ns == before


def test_a_derived_card_records_where_it_came_from(workspace):
    assert workspace.apply("board.deriveCard", {
        "id": "a", "newId": "c9", "kind": "result", "node": "a", "title": "a", "x": 0, "y": 5,
    }) is True
    card = next(c for c in workspace.snapshot()["cards"] if c["id"] == "c9")
    assert card["from"] == "a"
    assert card["node"] == "a"
    assert card["kind"] == "result"


def test_deriving_from_a_card_that_is_not_there_makes_nothing(workspace):
    assert workspace.apply("board.deriveCard", {"id": "ghost", "newId": "c9"}) is False
    assert all(card["id"] != "c9" for card in workspace.snapshot()["cards"])


def test_a_reference_survives_renaming_what_it_points_at(workspace):
    """The whole reason id and title are different fields.

    A derived card points at an id; renaming the source is a change to its
    title, and nothing that names it needs to hear about it.
    """
    workspace.apply("board.deriveCard", {"id": "a", "newId": "c9", "node": "a"})
    workspace.apply("card.setTitle", {"id": "a", "title": "Something else entirely"})
    cards = {card["id"]: card for card in workspace.snapshot()["cards"]}
    assert cards["c9"]["from"] == "a"
    assert cards["a"]["title"] == "Something else entirely"


def test_editing_the_document_text_is_an_ordinary_change(workspace):
    text = workspace.document.to_imgql() + '//@card id=z title="Typed" kind=note x=0 y=6\n'
    assert workspace.apply("workspace.setText", {"text": text}) is True
    assert any(card["title"] == "Typed" for card in workspace.snapshot()["cards"])
    assert workspace.apply("workspace.undo") is True
    assert all(card["title"] != "Typed" for card in workspace.snapshot()["cards"])


def test_a_workspace_with_no_path_still_has_a_file_to_write_to(tmp_path, monkeypatch):
    """Starting work must not begin with a decision.

    With nothing named, the scratch path is a real file in the platform's
    application-data directory -- so autosave has a destination from the first
    keystroke, and nothing had to be asked.
    """
    from voxlogica.ui import home

    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path / "appdata"))
    path = home.scratch_path()
    assert path.parent == tmp_path / "appdata" / "workspaces"
    assert path.suffix == home.SUFFIX
    # Nothing is created by asking where it would go: an abandoned session
    # leaves nothing behind.
    assert not path.exists()


def test_moving_a_workspace_takes_the_old_file_away(workspace, tmp_path):
    workspace.apply("board.moveCard", {"id": "a", "x": 1, "y": 1})
    workspace.flush()
    scratch = workspace.path
    assert scratch.exists()

    target = tmp_path / "repo" / "analysis.imgql"
    assert workspace.apply("workspace.moveTo", {"path": str(target)}) == str(target)
    assert target.read_text() == workspace.document.to_imgql()
    # One workspace, in one place: a copy left behind would be a second
    # workspace by tomorrow.
    assert not scratch.exists()
    assert workspace.snapshot()["path"] == str(target)


def test_moving_a_workspace_is_not_an_edit(workspace, tmp_path):
    workspace.apply("board.moveCard", {"id": "a", "x": 1, "y": 1})
    workspace.apply("workspace.moveTo", {"path": str(tmp_path / "elsewhere.imgql")})
    # Undo goes back through changes to the document, not through filing.
    assert workspace.apply("workspace.undo") is True
    assert workspace.apply("workspace.undo") is False


def test_a_scratch_workspace_is_a_file_before_it_holds_anything(tmp_path):
    """"Where is my work" must have an answer before there is any work.

    The file, and the directory under it, exist from the moment the workspace
    does -- so it can be revealed in a file manager, found again tomorrow, and
    written to by autosave without a first-time special case.
    """
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "appdata" / "workspaces" / "2026-01-01-000000.imgql"
    space = Workspace(path=path)
    assert path.exists()
    space.apply("board.addCard", {"id": "c1", "kind": "code", "title": "First"})
    space.flush()
    assert 'title="First"' in path.read_text()


def test_opening_an_existing_file_does_not_rewrite_it(tmp_path):
    """Creating the scratch must never touch somebody's own file."""
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "theirs.imgql"
    original = "// hand written, never opened in the UI\nlet a = 1\n"
    path.write_text(original)
    before = path.stat().st_mtime_ns
    Workspace(path=path)
    assert path.read_text() == original
    assert path.stat().st_mtime_ns == before


def test_a_folder_that_held_one_file_follows_it_out(tmp_path, monkeypatch):
    """A folder that existed for one file travels with that file.

    A project holding several does not: moving one of its files must not empty
    a project somebody is still using.
    """
    from voxlogica.ui import home
    from voxlogica.ui.workspace import Workspace

    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path / "appdata"))
    folder = home.workspaces() / "one-piece-of-work"
    folder.mkdir(parents=True)
    document = folder / "study.imgql"
    space = Workspace(path=document)
    (folder / "case.nii.gz").write_bytes(b"not really an image")

    target = tmp_path / "repo" / "study"
    space.apply("workspace.moveTo", {"path": str(target)})

    assert (target / home.DOCUMENT).exists()
    assert (target / "case.nii.gz").read_bytes() == b"not really an image"
    assert not folder.exists()


def test_a_project_with_other_files_in_it_stays_put(tmp_path, monkeypatch):
    from voxlogica.ui import home
    from voxlogica.ui.workspace import Workspace

    monkeypatch.setenv("VOXLOGICA_HOME", str(tmp_path / "appdata"))
    folder = home.workspaces() / "shared-project"
    folder.mkdir(parents=True)
    document = folder / "study.imgql"
    (folder / "another.imgql").write_text("let b = 2\n")
    space = Workspace(path=document)

    space.apply("workspace.moveTo", {"path": str(tmp_path / "repo" / "study.imgql")})

    assert folder.exists()
    assert (folder / "another.imgql").exists()
    assert not document.exists()


def test_what_was_written_is_what_the_document_says_it_is(tmp_path):
    """After a write, the file and the document must not disagree.

    A workspace that annotates itself on the way to disk was read from a file
    that did not contain those annotations. If the write leaves it remembering
    the text it came from, the next export -- or the next autosave -- hands back
    that older, emptier text and overwrites the file with it.
    """
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "scratch" / "workspace.imgql"
    space = Workspace(path=path)
    space.apply("card.setTitle", {"id": "program", "title": "Named"})
    space.flush()

    on_disk = path.read_text()
    assert 'title="Named"' in on_disk
    assert space.document.to_imgql() == on_disk
    assert space.snapshot()["source"] == on_disk

    # And a second write does not undo the first.
    space.flush()
    assert path.read_text() == on_disk


PAIR = """\
//@board cols=9 rows=8
//@card id=a title="Segmentation" kind=code x=0 y=0 w=4 h=3
let mask = threshold(flair, 0.6)
//@card id=b title="mask" kind=result x=4 y=0 w=3 h=2 node=mask view=state
"""


def _space(tmp_path, text):
    from voxlogica.ui.workspace import Workspace

    path = tmp_path / "doc.imgql"
    path.write_text(text)
    return Workspace(path=path)


def test_copying_cards_gives_imgql_text(tmp_path):
    """The clipboard's format is the file's format, so it is readable anywhere."""
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a", "b"]})
    assert '//@card id=a title="Segmentation" kind=code' in text
    assert "let mask = threshold(flair, 0.6)" in text
    assert "node=mask" in text
    # And it parses back as cards, which is what makes paste work anywhere.
    from voxlogica.ui import document as doc

    assert [card["title"] for card in doc.parse(text).cards] == ["Segmentation", "mask"]


def test_pasting_mints_new_ids_and_keeps_the_originals(tmp_path):
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a", "b"]})
    made = space.apply("board.pasteCards", {"text": text})
    cards = {card["id"]: card for card in space.snapshot()["cards"]}
    assert len(made) == 2
    assert set(made).isdisjoint({"a", "b"})
    assert cards["a"]["source"].strip() == "let mask = threshold(flair, 0.6)"


def test_a_pasted_binding_that_would_collide_is_renamed_with_its_references(tmp_path):
    """A pasted group must still compute what it computed where it came from."""
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a", "b"]})
    made = space.apply("board.pasteCards", {"text": text})
    cards = {card["id"]: card for card in space.snapshot()["cards"]}
    pasted_code = cards[made[0]]["source"]
    pasted_result = cards[made[1]]
    assert "let mask2 =" in pasted_code
    # The reference followed the rename, or the copy would show the original's
    # value and look like it worked.
    assert pasted_result["node"] == "mask2"
    assert cards["a"]["source"].strip() == "let mask = threshold(flair, 0.6)"


def test_cutting_takes_the_cards_out_and_hands_back_their_text(tmp_path):
    space = _space(tmp_path, PAIR)
    text = space.apply("board.cutCards", {"ids": ["b"]})
    assert "node=mask" in text
    assert [card["id"] for card in space.snapshot()["cards"]] == ["a"]
    # And it can be undone, because a cut is a change to the document.
    assert space.apply("workspace.undo") is True
    assert [card["id"] for card in space.snapshot()["cards"]] == ["a", "b"]


def test_plain_program_text_pasted_from_anywhere_becomes_a_card(tmp_path):
    space = _space(tmp_path, PAIR)
    made = space.apply("board.pasteCards", {"text": "let extra = 3\n"})
    cards = {card["id"]: card for card in space.snapshot()["cards"]}
    assert len(made) == 1
    assert cards[made[0]]["kind"] == "code"
    assert "let extra = 3" in cards[made[0]]["source"]


def test_pasting_puts_the_cards_on_the_page_asked_for(tmp_path):
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a"]})
    made = space.apply("board.pasteCards", {"text": text, "page": 2})
    card = next(c for c in space.snapshot()["cards"] if c["id"] == made[0])
    assert card["page"] == 2


def test_cards_dropped_on_another_file_are_merged_by_the_same_rules(tmp_path):
    """Dragging cards into a file in the sidebar is pasting into it.

    The text is the clipboard's text and the merge is `Document`'s, so the ids
    that would collide are renamed and both cards survive -- the same outcome a
    paste onto the board gives, because it is the same code.
    """
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a", "b"]})

    elsewhere = tmp_path / "other.imgql"
    elsewhere.write_text(PAIR)  # already holds cards called a and b
    made = space.apply("library.pasteCards", {"path": str(elsewhere), "text": text})

    from voxlogica.ui import document as doc

    landed = doc.parse(elsewhere.read_text())
    assert len(made) == 2
    assert [card["id"] for card in landed.cards] == ["a", "b", *made]
    # Both of each: nothing was overwritten by something with the same name.
    assert len(landed.cards) == 4
    # And the pasted program still computes what it computed where it came from.
    pasted = {card["id"]: card for card in landed.cards}
    assert "let mask2 =" in pasted[made[0]]["source"]
    assert pasted[made[1]]["node"] == "mask2"
    # The board it was dragged off is untouched: dropping is not moving.
    assert [card["id"] for card in space.snapshot()["cards"]] == ["a", "b"]


def test_cards_dropped_on_the_file_that_is_open_go_through_the_board(tmp_path):
    """Otherwise the next autosave would write the workspace over the drop."""
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a"]})
    made = space.apply("library.pasteCards", {"path": str(space.path), "text": text})
    assert len(made) == 1
    assert made[0] in {card["id"] for card in space.snapshot()["cards"]}
    space.flush()
    assert space.path.read_text() == space.document.to_imgql()


def test_cards_dropped_on_a_file_that_is_not_there_yet_make_it(tmp_path):
    """A project row makes a file to drop into, and this is that file."""
    space = _space(tmp_path, PAIR)
    text = space.apply("board.copyCards", {"ids": ["a"]})
    fresh = tmp_path / "Study" / "new.imgql"
    made = space.apply("library.pasteCards", {"path": str(fresh), "text": text})
    assert len(made) == 1
    assert "threshold(flair, 0.6)" in fresh.read_text()
