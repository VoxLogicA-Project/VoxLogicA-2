"""`voxlogica mcp`: a stdio MCP server that talks to whichever instance is live.

MCP clients are configured once, with a command rather than a URL, because a URL
would name a port that changes on every run. This bridge is that command: it
finds the running instances (see :mod:`registration`), talks to the newest over
its loopback HTTP API, and exposes exactly the tools the in-process server
exposes -- the same action manifest, the same observations, the same screenshots
taken by a real browser.

It holds no state. If no instance is running, every tool says so plainly instead
of failing in a way an agent has to interpret.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .actions import ACTIONS, json_schema
from .manual import manual
from .registration import instances

logger = logging.getLogger(__name__)

TIMEOUT = 15.0


class Instance:
    """The live workspace this bridge is speaking for."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def get(self, path: str) -> Any:
        with urlrequest.urlopen(f"{self.url}{path}", timeout=TIMEOUT) as response:
            return json.loads(response.read())

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        request = urlrequest.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())


def current() -> Instance | None:
    live = instances()
    return Instance(live[0]["url"]) if live else None


def _no_instance() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "no VoxLogicA instance is running; start one with `voxlogica serve` "
        "or `voxlogica run program.imgql`",
    }


def build_stdio_server():
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server("voxlogica")

    reads = {
        "workspace.instances": (
            "Every running VoxLogicA instance: pid, port, url and the program it "
            "is showing. Call this first when there might be more than one.",
            {"type": "object", "properties": {}},
        ),
        "workspace.document": (
            "The whole workspace: board geometry, every card with its mode and "
            "contents, and the current view.",
            {"type": "object", "properties": {}},
        ),
        "workspace.imgql": (
            "The document as .imgql text, byte for byte what saving would write.",
            {"type": "object", "properties": {}},
        ),
        "workspace.grid": (
            "The lattice: columns, rows, cell pitch, and which cells each card "
            "occupies. Read this before moving or resizing anything.",
            {"type": "object", "properties": {}},
        ),
        "card.get": (
            "One card: its kind, its geometry and its contents.",
            {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        ),
        "voxlogica.manual": (
            "The manual: everything the application does, the same page the "
            "user reads. Read this first -- an agent that guesses at the "
            "vocabulary guesses wrong.",
            {"type": "object", "properties": {}},
        ),
        "ui.screenshot": (
            "A PNG of what a connected browser is showing: the whole board "
            "('board'), the page ('page'), or one card by id.",
            {"type": "object", "properties": {"target": {"type": "string"}}},
        ),
    }

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
        params = arguments or {}
        dotted = name.replace("_", ".", 1) if "_" in name else name

        if dotted == "workspace.instances":
            return [TextContent(type="text", text=json.dumps(instances(), indent=2))]

        if dotted == "voxlogica.manual":
            # Answerable with no instance running: an agent should be able to
            # find out what this is before it has anything to point at.
            return [TextContent(type="text", text=manual())]

        instance = current()
        if instance is None:
            return [TextContent(type="text", text=json.dumps(_no_instance(), indent=2))]

        try:
            if dotted == "workspace.document":
                payload: Any = instance.get("/api/workspace")
            elif dotted == "workspace.imgql":
                payload = instance.post("/api/action", {"name": "workspace.export"})
            elif dotted == "workspace.grid":
                snapshot = instance.get("/api/workspace").get("workspace") or {}
                payload = {
                    "board": snapshot.get("board"),
                    "view": snapshot.get("view"),
                    "pitch": "4rem",
                    "gutter": "0.5rem",
                    "note": "positions and sizes are in cells",
                    "cards": {
                        card["id"]: [card.get("x"), card.get("y"), card.get("w"), card.get("h")]
                        for card in snapshot.get("cards", [])
                    },
                }
            elif dotted == "card.get":
                snapshot = instance.get("/api/workspace").get("workspace") or {}
                found = [c for c in snapshot.get("cards", []) if c.get("id") == params.get("id")]
                payload = found[0] if found else {"ok": False, "error": "no such card"}
            elif dotted == "ui.screenshot":
                payload = instance.post("/api/capture", {"target": params.get("target")})
            elif dotted in ACTIONS:
                payload = instance.post("/api/action", {"name": dotted, "params": params})
            else:
                payload = {"ok": False, "error": f"no such tool: {name}"}
        except (urlerror.URLError, OSError) as error:
            payload = {"ok": False, "error": f"instance unreachable: {error}"}

        return [TextContent(type="text", text=json.dumps(payload, indent=2))]

    return server


def main() -> int:
    """Run the bridge on stdio until the client goes away."""
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_stdio_server()

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)
    return 0
