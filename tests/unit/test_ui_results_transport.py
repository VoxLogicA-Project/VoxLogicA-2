"""The wire between a node changing state and a card redrawing.

The store's rules are in `test_ui_results.py`; what is checked here is the part
that only exists once there is a socket: that a subscription is answered rather
than merely recorded, that an update reaches a connected client, and that a
client which never subscribed is not sent a run's worth of node traffic.

These are silent when broken. A card that never updates looks like a slow
computation, and a socket carrying every node of a large plan looks like nothing
at all until somebody opens a second window.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voxlogica.ui.app import build_app
from voxlogica.ui.bundler import Bundler
from voxlogica.ui.hub import Hub
from voxlogica.ui.results import Results
from voxlogica.ui.workspace import Workspace

PROGRAM = """\
//@board cols=9 rows=8
//@card id=a kind=code x=0 y=0 w=4 h=3
let a = 1
"""


@pytest.fixture()
def wired(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    hub = Hub()
    results = Results(hub, probe=lambda h: h == "cached", fetch=lambda h: 7)
    app = build_app(
        hub=hub,
        bundler=Bundler(source_root=None),
        describe=lambda: {},
        workspace=Workspace(hub=hub, path=path, results=results),
        results=results,
    )
    with TestClient(app) as client:
        yield client, results


def _drain_until(socket, wanted, *, limit=20):
    """Messages of one type, ignoring the hello/workspace traffic around them."""
    for _ in range(limit):
        message = socket.receive_json()
        if message.get("type") == wanted:
            return message
    raise AssertionError(f"no {wanted} message arrived")


def test_subscribing_is_answered_with_the_current_state(wired):
    client, _results = wired
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "results.subscribe", "hashes": ["cached"]})
        message = _drain_until(socket, "result")
        assert message["hash"] == "cached"
        assert message["state"] == "done"
        assert message["value"] == 7


def test_an_update_reaches_a_subscribed_client(wired):
    client, results = wired
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "results.subscribe", "hashes": ["live"]})
        first = _drain_until(socket, "result")
        assert first["state"] == "unknown"

        results.observe("live", "computing")
        assert _drain_until(socket, "result")["state"] == "computing"

        results.observe("live", "done", value=3)
        done = _drain_until(socket, "result")
        assert done["state"] == "done" and done["value"] == 3


def test_nothing_is_pushed_for_a_node_nobody_asked_about(wired):
    """The property that keeps an open window from costing a fraction of a run."""
    client, results = wired
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "results.subscribe", "hashes": ["watched"]})
        _drain_until(socket, "result")

        results.observe("unwatched", "computing")
        results.observe("watched", "computing")
        # The next result frame is the watched one: had the unwatched node been
        # published, it would be sitting in front of it.
        assert _drain_until(socket, "result")["hash"] == "watched"


def test_unsubscribing_over_the_socket_stops_the_traffic(wired):
    client, results = wired
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "results.subscribe", "hashes": ["a", "b"]})
        _drain_until(socket, "result")
        _drain_until(socket, "result")

        socket.send_json({"type": "results.unsubscribe", "hashes": ["a"]})
        # Round-trip something else first, so the unsubscribe has certainly been
        # processed before the transition it is supposed to suppress.
        socket.send_json({"type": "ping"})
        _drain_until(socket, "pong")

        results.observe("a", "done", value=1)
        results.observe("b", "done", value=2)
        assert _drain_until(socket, "result")["hash"] == "b"


def test_the_snapshot_carries_the_document_bindings(wired):
    """A card names a binding; only the reducer can say which node that is, so
    the map has to travel with the text it describes."""
    client, _results = wired
    response = client.get("/api/workspace")
    assert response.status_code == 200
    nodes = response.json()["workspace"]["nodes"]
    assert "a" in nodes and len(nodes["a"]) == 64
