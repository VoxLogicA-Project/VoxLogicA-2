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
