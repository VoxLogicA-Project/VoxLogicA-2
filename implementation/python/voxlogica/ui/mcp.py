"""MCP over the same port the UI is served on.

The rule this module implements, from doc/dev/ui-workspace.md section 8: **an
agent can do anything the user can do, and see anything the user can see.** Both
halves are load-bearing, and each is arranged so that it cannot quietly become
false:

* *Do* -- every tool that changes something is generated from ``actions.ACTIONS``,
  the same list the browser calls. Adding an action gives the agent that action;
  there is no second list to remember to update.
* *See* -- the document, every card's content and mode, the board's geometry and
  the current view come from the same snapshot the browser renders. Screenshots
  are the one thing this process cannot produce alone, so it asks a connected
  tab (see ``capture.ts``): the answer is then literally what the user is looking
  at, rather than a re-rendering that agrees with the layout but not with the
  screen.

In-process, on the UI's own port, for the reason in ui-architecture.md section 1:
a second process would mean a lifecycle to manage, an IPC protocol to version,
and two places for one workspace to live.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from .actions import ACTIONS, json_schema
from .manual import manual
from . import tools

logger = logging.getLogger(__name__)

#: How long an agent waits for a tab to answer a screenshot request. Long enough
#: for a slow page, short enough that "nobody is looking" is an answer and not a
#: hang.
CAPTURE_TIMEOUT = 10.0


class CaptureBroker:
    """Outstanding screenshot requests, keyed by id.

    The server asks; whichever tab answers first wins. A capture is not a
    mutation, so a second tab answering late is discarded rather than merged.
    """

    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future] = {}

    def begin(self) -> tuple[str, asyncio.Future]:
        request_id = uuid.uuid4().hex[:8]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = future
        return request_id, future

    def settle(self, request_id: str, message: dict[str, Any]) -> None:
        future = self._waiting.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(message)

    def abandon(self, request_id: str) -> None:
        self._waiting.pop(request_id, None)


async def screenshot(hub, captures: "CaptureBroker", target: str | None) -> dict[str, Any]:
    """A PNG of the board, or of one card, as seen by a connected browser.

    `target` is `None`/`"board"` for the whole board, `"page"` for everything on
    screen, or a card id. With no tab open there is nothing to photograph, and
    saying so is a better answer than a picture of what the layout *would* look
    like.
    """
    if hub.client_count() == 0:
        return {"ok": False, "error": "no browser is connected, so there is nothing to photograph"}
    request_id, future = captures.begin()
    hub.publish({"type": "capture", "id": request_id, "target": target})
    try:
        reply = await asyncio.wait_for(future, timeout=CAPTURE_TIMEOUT)
    except asyncio.TimeoutError:
        captures.abandon(request_id)
        return {"ok": False, "error": "the browser did not answer in time"}
    if not reply.get("ok"):
        return {"ok": False, "error": reply.get("error", "capture failed")}
    return {"ok": True, "png_base64": reply.get("png"), "target": target or "board"}


class WorkspaceMCP:
    """The MCP surface over one workspace."""

    def __init__(self, workspace, hub, captures: CaptureBroker) -> None:
        self._workspace = workspace
        self._hub = hub
        self._captures = captures

    # ------------------------------------------------------------------ seeing

    def document(self) -> dict[str, Any]:
        """The whole document: board geometry, every card, the current view."""
        return self._workspace.snapshot()

    def imgql(self) -> str:
        """The document as the file it would be saved as, byte for byte."""
        return self._workspace.document.to_imgql()

    def card(self, card_id: str) -> dict[str, Any]:
        """One card: its mode, its geometry, its contents."""
        for card in self._workspace.document.cards:
            if card["id"] == card_id:
                return card
        raise KeyError(f"no card with id {card_id}")

    def manual(self) -> str:
        return manual()

    def grid(self) -> dict[str, Any]:
        """The lattice an agent has to reason in before it moves anything.

        Cell sizes are reported in the units the model uses (cells) *and* in the
        CSS length one cell resolves to, because an agent looking at a screenshot
        needs to convert pixels back into cells.
        """
        board = self._workspace.document.board
        occupied: dict[str, list[int]] = {}
        for card in self._workspace.document.cards:
            occupied[card["id"]] = [
                card.get("x", 0),
                card.get("y", 0),
                card.get("w", 0),
                card.get("h", 0),
            ]
        return {
            "cols": board["cols"],
            "rows": board["rows"],
            "pitch": "4rem",
            "gutter": "0.5rem",
            "note": "positions and sizes are in cells; a card w cells wide is w*pitch - gutter",
            "cards": occupied,
            "view": dict(self._workspace.view),
        }

    async def screenshot(self, target: str | None = None) -> dict[str, Any]:
        return await screenshot(self._hub, self._captures, target)

    # ------------------------------------------------------------------- doing

    def act(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._workspace.apply(name, params)
        except Exception as error:
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        return {"ok": True, "result": result, "workspace": self._workspace.snapshot()}


def build_transport(workspace, hub, captures: CaptureBroker):
    """The ASGI handler for ``/mcp`` and the manager whose lifetime it needs.

    Returns ``(handler, manager)``, or ``None`` if MCP cannot be built at all --
    a failure here must cost the user their agent tools, never their UI.

    The manager owns a task group, and a task group has to be entered and left by
    the same task: opening it lazily on the first request is what makes anyio
    raise "attempted to exit a cancel scope that isn't the current task's". So
    the caller enters it in the app's lifespan, on the loop that serves the
    requests, and this function only builds the pieces.

    Stateless streamable HTTP: an agent connects, calls tools, and goes away, and
    nothing about the workspace lives in the session -- the workspace *is* the
    session, and it is already here.
    """
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except Exception as error:  # noqa: BLE001 - optional surface
        logger.warning("MCP unavailable (%s); the UI is unaffected", error)
        return None

    try:
        server = build_server(workspace, hub, captures)
        manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not build the MCP server (%s)", error)
        return None

    async def handle(scope, receive, send) -> None:
        await manager.handle_request(scope, receive, send)

    return handle, manager


def build_server(workspace, hub, captures: CaptureBroker):
    """An `mcp.server.Server` exposing every action and every observation.

    Imported lazily by the caller: the `mcp` package is a dependency of the
    project, but a UI that fails to start an MCP server must still be a UI.
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    surface = WorkspaceMCP(workspace, hub, captures)
    server = Server("voxlogica-workspace")

    # One catalogue, shared with the stdio bridge: see tools.py for why
    # there are two servers and must not be two vocabularies.
    reads = tools.mounted()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
            Tool(name=name.replace(".", "_"), description=doc, inputSchema=schema)
            for name, (doc, schema) in reads.items()
        ]
        tools.extend(
            Tool(
                name=name.replace(".", "_"),
                description=action.doc,
                inputSchema=json_schema(action),
            )
            for name, action in ACTIONS.items()
        )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        import json

        params = arguments or {}
        dotted = name.replace("_", ".", 1) if "_" in name else name

        if dotted == "workspace.document":
            payload: Any = surface.document()
        elif dotted == "workspace.imgql":
            payload = surface.imgql()
        elif dotted == "workspace.grid":
            payload = surface.grid()
        elif dotted == "voxlogica.manual":
            payload = surface.manual()
        elif dotted == "card.get":
            payload = surface.card(params["id"])
        elif dotted == "ui.screenshot":
            payload = await surface.screenshot(params.get("target"))
        elif dotted in ACTIONS:
            payload = surface.act(dotted, params)
        else:
            payload = {"ok": False, "error": f"no such tool: {name}"}

        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
        return [TextContent(type="text", text=text)]

    return server
