"""What an agent can *read*, declared once for both MCP surfaces.

There are two servers and there have to be. `mcp.py` is mounted on a running
instance and holds the workspace object itself; `bridge.py` is a stdio process an
MCP client launches, which finds a live instance and forwards to it. The
registration names `voxlogica mcp` rather than a URL, because a URL would name a
port that changes on the next run -- so the bridge cannot be deleted, and the
mount cannot answer for an instance that is not this one.

Two *transports*, then. Not two vocabularies. Each surface used to carry its own
hand-written table of these, and every tool added since had to be written twice;
that is exactly how `voxlogica.manual` came to exist on one of them and not the
other, with nothing to say so. The tables were identical when they were copied,
which is what made copying them cheap and what made the divergence invisible
later.

The *actions* were never at risk: both surfaces generate those from
`actions.ACTIONS`, which is the same idea one layer down. This file is the
missing half of it.

Dispatch stays where it belongs -- direct against the workspace in `mcp.py`,
proxied over HTTP in `bridge.py` -- because that genuinely differs. Only the
catalogue is shared.
"""

from __future__ import annotations

from typing import Any

#: `name -> (what it is for, its JSON schema)`. Names are dotted here and become
#: underscored on the wire, in one place per surface.
READS: dict[str, tuple[str, dict[str, Any]]] = {
    "voxlogica.manual": (
        "The manual: everything the application does, the same page the user "
        "reads. Read this first -- an agent that guesses at the vocabulary "
        "guesses wrong.",
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
        {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    ),
    "ui.screenshot": (
        "A PNG of what a connected browser is showing: the whole board "
        "('board'), the page ('page'), or one card by id.",
        {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "'board' (default), 'page', or a card id",
                }
            },
        },
    ),
}

#: The bridge alone can answer this: it is the only one of the two not already
#: inside an instance, so it is the only one that can say how many there are.
BRIDGE_ONLY: dict[str, tuple[str, dict[str, Any]]] = {
    "workspace.instances": (
        "Every running VoxLogicA instance: pid, port, url and the program it is "
        "showing. Call this first when there might be more than one.",
        {"type": "object", "properties": {}},
    ),
}


def mounted() -> dict[str, tuple[str, dict[str, Any]]]:
    """The catalogue for the server inside an instance."""
    return dict(READS)


def stdio() -> dict[str, tuple[str, dict[str, Any]]]:
    """The catalogue for the bridge: the same, plus the one only it can answer."""
    return {**BRIDGE_ONLY, **READS}
