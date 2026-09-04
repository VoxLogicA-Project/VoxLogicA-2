"""The in-process MCP endpoint actually speaks MCP.

Written after the first version did not: the session manager's task group was
being opened lazily on the first request, which anyio rejects because a task
group must be entered and left by the same task. It now lives in the app's
lifespan, and these tests would have caught that -- `TestClient` as a context
manager runs the lifespan, and a bare `client.post` would not.
"""

import json

import pytest
from fastapi.testclient import TestClient

from voxlogica.ui.app import build_app
from voxlogica.ui.bundler import Bundler
from voxlogica.ui.hub import Hub
from voxlogica.ui.workspace import Workspace

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream"}

PROGRAM = """\
//@board cols=9 rows=8
//@card id=a kind=code x=0 y=0 w=4 h=3
let a = 1
"""


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    app = build_app(
        hub=Hub(),
        bundler=Bundler(source_root=None),
        describe=lambda: {},
        workspace=Workspace(path=path),
    )
    with TestClient(app) as test_client:
        yield test_client


def _result(response):
    assert response.status_code == 200, response.text
    return response.json()["result"]


@pytest.mark.parametrize("url", ["/mcp/", "/mcp"])
def test_an_agent_can_initialise_a_session_at_either_url(client, url):
    # A mount answers "/mcp/..." but not "/mcp"; clients are configured with
    # both spellings in the wild, so both have to work.
    body = _result(client.post(url, json=INITIALIZE, headers=HEADERS))
    assert body["serverInfo"]["name"] == "voxlogica-workspace"


def test_every_action_is_offered_as_a_tool(client):
    from voxlogica.ui.actions import ACTIONS

    client.post("/mcp/", json=INITIALIZE, headers=HEADERS)
    listed = _result(
        client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=HEADERS,
        )
    )
    names = {tool["name"] for tool in listed["tools"]}
    missing = {name.replace(".", "_") for name in ACTIONS} - names
    assert not missing, f"actions the agent cannot reach: {sorted(missing)}"
    # And the observations the spec requires.
    assert {
        "workspace_document",
        "workspace_imgql",
        "workspace_grid",
        "card_get",
        "ui_screenshot",
    } <= names


def test_an_agent_sees_the_document_the_user_sees(client):
    client.post("/mcp/", json=INITIALIZE, headers=HEADERS)
    reply = _result(
        client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "workspace_document", "arguments": {}},
            },
            headers=HEADERS,
        )
    )
    document = json.loads(reply["content"][0]["text"])
    assert [card["id"] for card in document["cards"]] == ["a"]
    assert document["board"] == {"cols": 9, "rows": 8}


def test_an_agent_moving_a_card_changes_the_document(client):
    client.post("/mcp/", json=INITIALIZE, headers=HEADERS)
    reply = _result(
        client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "board_moveCard",
                    "arguments": {"id": "a", "x": 3, "y": 2},
                },
            },
            headers=HEADERS,
        )
    )
    outcome = json.loads(reply["content"][0]["text"])
    assert outcome["ok"] is True
    card = next(card for card in outcome["workspace"]["cards"] if card["id"] == "a")
    assert (card["x"], card["y"]) == (3, 2)


def test_a_screenshot_with_nobody_looking_says_so_rather_than_inventing_one(client):
    client.post("/mcp/", json=INITIALIZE, headers=HEADERS)
    reply = _result(
        client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "ui_screenshot", "arguments": {}},
            },
            headers=HEADERS,
        )
    )
    outcome = json.loads(reply["content"][0]["text"])
    assert outcome["ok"] is False
    assert "no browser" in outcome["error"]
