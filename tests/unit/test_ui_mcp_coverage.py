"""An agent can do what a person can, and read the same manual.

R4 is not "there is an MCP server". It is that the agent and the person are two
views of *one* workspace -- so anything a person can do that the agent cannot is
a divergence, and the two of them stop being able to talk about the same thing.

The test that matters is the boring one: every action is a tool. It is boring
because the tools are generated from the action list, which is the design
working; it is here because the day somebody hand-writes a tool, or hand-writes
a gesture that skips the action list, it stops being true and nothing says so.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voxlogica.ui.actions import ACTIONS
from voxlogica.ui.hub import Hub
from voxlogica.ui.mcp import CaptureBroker, WorkspaceMCP, build_server
from voxlogica.ui.workspace import Workspace

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "doc" / "user" / "manual.md"
APP = ROOT / "implementation" / "ui" / "src" / "App.svelte"

PROGRAM = "//@board cols=9 rows=8\n//@card id=c1 kind=code x=0 y=0\nlet a = 1\n"


@pytest.fixture()
def server(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    hub = Hub()
    workspace = Workspace(hub=hub, path=path)
    return build_server(workspace, hub, CaptureBroker()), workspace


async def _tools(server) -> dict[str, object]:
    # The decorator registers the handler; this is how the session would ask.
    handler = server.request_handlers
    from mcp.types import ListToolsRequest

    result = await handler[ListToolsRequest](ListToolsRequest(method="tools/list"))
    return {tool.name: tool for tool in result.root.tools}


@pytest.mark.anyio
async def test_every_action_is_a_tool(server):
    """The whole of R4, in one assertion."""
    tools = await _tools(server[0])
    missing = [name for name in ACTIONS if name.replace(".", "_") not in tools]
    assert not missing, f"an agent cannot do these: {sorted(missing)}"


@pytest.mark.anyio
async def test_every_tool_says_what_it_is_for(server):
    """A tool with no description is a tool an agent will use wrongly."""
    tools = await _tools(server[0])
    silent = [name for name, tool in tools.items() if not (tool.description or "").strip()]
    assert not silent, silent


@pytest.mark.anyio
async def test_the_manual_is_a_tool(server):
    """Same document, both readers. An agent that has to guess at the
    vocabulary guesses wrong, and there is no reason to make it guess."""
    tools = await _tools(server[0])
    assert "voxlogica_manual" in tools


def test_the_manual_tool_returns_the_manual(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(PROGRAM)
    surface = WorkspaceMCP(Workspace(path=path), Hub(), CaptureBroker())
    text = surface.manual()
    assert "VoxLogicA" in text
    assert text == MANUAL.read_text(), "the agent is reading a copy, not the manual"


def test_the_view_a_person_changes_is_a_view_an_agent_can_read():
    """Anything a person can do that lives only in a component is a gap.

    `showing` was one: Tab swapped the board for the document in browser-local
    state, so an agent could neither see which the user was looking at nor put
    them back. It is view state on the server now, like the page and the zoom,
    and this keeps it that way.
    """
    if not APP.exists():
        pytest.skip("no UI sources here (running from a wheel)")
    text = APP.read_text()
    assert "let showing = $state(" not in text, (
        "`showing` is browser-local again; it is workspace view state, "
        "reachable as the `view.show` action"
    )
    assert "view.show(" in text


def test_the_two_mcp_surfaces_offer_the_same_tools():
    """There are two, and they must not be two vocabularies.

    `mcp.py` is mounted on the running instance; `bridge.py` is the stdio
    server an MCP client launches, and it is the one agents actually reach --
    the registration names `voxlogica mcp`, not a URL, because a URL would name
    a port that changes. So both have to exist.

    They used to carry a hand-written table each, which is how the manual tool
    came to be on one of them only. The catalogue is shared now (tools.py), so
    this asserts a property the structure already provides -- and it is still
    worth asserting, because the next person to add a tool will find out here
    rather than in a client.
    """
    from voxlogica.ui import tools

    only_stdio = set(tools.stdio()) - set(tools.mounted())
    assert only_stdio == {"workspace.instances"}, (
        "the bridge answers something the mount does not, and only "
        "workspace.instances has a reason to be that: it is the one question "
        "asked from outside an instance"
    )
    assert set(tools.mounted()) - set(tools.stdio()) == set()


def test_neither_surface_writes_its_own_catalogue():
    """Structure rather than vigilance.

    A test that catches drift is weaker than a shape that prevents it, so the
    tables live in one module and this makes going back visible.
    """
    from voxlogica.ui import bridge, mcp

    for module in (mcp, bridge):
        source = Path(module.__file__).read_text()
        assert "tools." in source, f"{module.__name__} is not using the shared catalogue"
        assert '": (' not in source.split("def build")[1][:2000], (
            f"{module.__name__} has grown a tool table of its own again"
        )


def test_the_manual_is_read_from_one_place():
    """Both surfaces call the same function, so neither can serve a stale copy."""
    from voxlogica.ui import bridge, mcp
    from voxlogica.ui.manual import manual as read_manual

    assert "from .manual import manual" in Path(mcp.__file__).read_text()
    assert "from .manual import manual" in Path(bridge.__file__).read_text()
    assert read_manual() == MANUAL.read_text()


def test_the_manual_names_the_tools_an_agent_starts_with():
    """A first-contact vocabulary, in the page the agent is told to read."""
    text = MANUAL.read_text()
    for tool in ("workspace_document", "workspace_grid", "card_get", "results_wait"):
        assert tool in text, f"the manual does not mention {tool}"


@pytest.fixture
def anyio_backend():
    return "asyncio"
