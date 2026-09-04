"""The MCP endpoint, against a server that is actually serving.

`test_ui_mcp_endpoint.py` drives the app through `TestClient`, which is the
right way to test routes -- and it is exactly why this file has to exist as
well. `TestClient` used as a context manager **runs the lifespan**. The real
server ran uvicorn with `lifespan="off"`, so the MCP session manager's task
group was never entered and every request to `/mcp` answered
`500 Task group is not initialized`.

The tests passed. The endpoint did not work. For months, silently, because
nothing anywhere drove the thing that was actually shipped.

So: bind a port, start the server the way `voxlogica serve` starts it, and
speak the protocol over a socket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from voxlogica.ui.bundler import Bundler
from voxlogica.ui.hub import Hub
from voxlogica.ui.server import UIServer, bind_loopback
from voxlogica.ui.workspace import Workspace

PROGRAM = "//@board cols=9 rows=8\n//@card id=c1 kind=code x=0 y=0\nlet a = 1\n"

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


@pytest.fixture()
def served(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    hub = Hub()
    server = UIServer(
        hub=hub,
        bundler=Bundler(source_root=None),
        sock=bind_loopback(0),
        workspace=Workspace(hub=hub, path=path),
    )
    server.start()
    try:
        yield server.url.rstrip("/")
    finally:
        server.stop()


def speak(url: str, body: dict, session: str | None = None):
    """One JSON-RPC message, and whatever came back."""
    headers = dict(HEADERS)
    if session:
        headers["mcp-session-id"] = session
    request = urllib.request.Request(
        f"{url}/mcp/", data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, response.headers.get("mcp-session-id"), response.read().decode()


def payload(text: str) -> dict:
    """The body, whether it arrived as JSON or as one SSE frame."""
    for line in text.splitlines():
        line = line[6:] if line.startswith("data: ") else line
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON in {text!r}")


def test_the_mounted_endpoint_answers_at_all(served):
    """The whole point. It answered 500 to this, and the suite was green."""
    status, _session, body = speak(
        served,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert status == 200, body
    assert payload(body)["result"]["serverInfo"]["name"] == "voxlogica-workspace"


def test_an_agent_can_list_the_tools_over_http(served):
    """Through a whole session, because the failure was in the session
    manager's lifetime rather than in any one route."""
    _status, session, body = speak(
        served,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert payload(body)["result"]

    speak(served, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    _status, _session, listing = speak(
        served, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session
    )
    names = {tool["name"] for tool in payload(listing)["result"]["tools"]}
    assert "voxlogica_manual" in names
    assert "card_run" in names, "the action tools are missing from the mounted server"


def test_the_lifespan_is_not_switched_off(served):
    """A one-word regression that costs the whole surface.

    Named here rather than left to the behavioural tests above because the
    symptom -- 500 on every MCP request -- points nowhere near the cause, and
    the cause is one keyword in another file.
    """
    from pathlib import Path

    from voxlogica.ui import server as server_module

    source = Path(server_module.__file__).read_text()
    assert 'lifespan="off"' not in source
