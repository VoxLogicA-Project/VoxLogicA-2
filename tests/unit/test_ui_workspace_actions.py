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
