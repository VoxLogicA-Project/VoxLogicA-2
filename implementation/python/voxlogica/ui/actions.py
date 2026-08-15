"""The action vocabulary: one list, three consumers.

Everything that can change a workspace is named here, once, as data. The browser
calls these actions, the MCP server exposes these actions, and neither owns the
list -- which is the only way two front ends stay able to do the same things a
year from now. A test asserts the TypeScript facade covers every entry, so an
action cannot quietly exist on one side only.

The namespace is the shape of the user's work (`board`, `card`, `view`,
`workspace`), not the shape of this module. Someone looking for "how do I move a
card" should find `board.moveCard` without knowing what a Document is.

See doc/dev/ui-workspace.md sections 3 and 5.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from . import guard

#: Parameter types are deliberately few: they have to survive JSON, a TypeScript
#: signature and an MCP tool schema without a type system in the middle.
JSON_TYPES = {
    "string": "string",
    "int": "integer",
    "number": "number",
    "bool": "boolean",
    "placements": "array",
}


@dataclass(frozen=True)
class Action:
    name: str
    params: dict[str, str]
    required: tuple[str, ...]
    doc: str
    apply: Callable[["Workspace", dict[str, Any]], Any]  # noqa: F821
    #: Does this change the document? Only these are worth undoing. Turning a
    #: page is not an edit, and an undo stack that made you step back through
    #: everything you looked at would be a stack nobody would use twice.
    mutates: bool = True


def _move(workspace, params):
    return workspace.document.place(params["id"], x=params["x"], y=params["y"])


def _resize(workspace, params):
    return workspace.document.place(params["id"], w=params["w"], h=params["h"])


def _arrange(workspace, params):
    """Place several cards at once, or none of them.

    Dragging a card that pushes others out of the way is one intention, and it
    has to reach the document as one change. Sent as separate moves it is not
    just chattier: between the first and the last the board really does hold an
    overlapping layout, and anything watching -- another browser, an agent,
    whoever saves the file next -- can see it. Refusing the whole batch when one
    id is unknown is the same argument from the other side.
    """
    placements = params.get("cards") or []
    ids = [str(spot["id"]) for spot in placements]
    if any(workspace.document.find(card_id) is None for card_id in ids):
        return False
    for spot in placements:
        workspace.document.place(
            str(spot["id"]),
            **{key: int(spot[key]) for key in ("x", "y", "w", "h") if key in spot},
        )
    return True


def _add(workspace, params):
    return workspace.document.add_card(
        params["id"],
        params.get("kind", "code"),
        x=params.get("x", 0),
        y=params.get("y", 0),
        w=params.get("w"),
        h=params.get("h"),
        page=params.get("page"),
        node=params.get("node"),
        title=params.get("title"),
    )


def _duplicate(workspace, params):
    return workspace.document.duplicate_card(
        params["id"],
        params["newId"],
        x=params.get("x"),
        y=params.get("y"),
        page=params.get("page"),
    )


def _derive(workspace, params):
    """A card that exists to show something another card produces.

    The new card records where it came from in `from`, so the relationship is in
    the document rather than in somebody's memory: a card can be renamed, moved
    or re-titled and whatever derives from it still points at it, because it
    points at the id and the id is not the name.
    """
    if workspace.document.find(params["id"]) is None:
        return False
    return workspace.document.add_card(
        params["newId"],
        params.get("kind", "result"),
        title=params.get("title"),
        node=params.get("node"),
        x=params.get("x"),
        y=params.get("y"),
        w=params.get("w"),
        h=params.get("h"),
        page=params.get("page"),
        **{"from": params["id"]},
    )


def _remove(workspace, params):
    return workspace.document.remove_card(params["id"])


def _set_page(workspace, params):
    return workspace.document.set_attr(params["id"], "page", params["page"])


def _set_title(workspace, params):
    return workspace.document.set_attr(params["id"], "title", params["title"])


def _set_source(workspace, params):
    return workspace.document.set_source(params["id"], params["text"])


def _bind_node(workspace, params):
    return workspace.document.set_attr(params["id"], "node", params["node"])


def _set_kind(workspace, params):
    return workspace.document.set_attr(params["id"], "kind", params["kind"])


def _set_view_mode(workspace, params):
    return workspace.document.set_attr(params["id"], "view", params["view"])


def _go_to_page(workspace, params):
    workspace.view["page"] = params["page"]
    return True


def _set_zoom(workspace, params):
    workspace.view["zoom"] = params["zoom"]
    return True


def _select(workspace, params):
    """Select nothing, one card, or several.

    A list rather than a single id, because everything you can do to one card
    you eventually want to do to three, and a UI that has to grow a second
    concept for that ends up with two rules for what "the current card" means.
    """
    ids = params.get("ids")
    if ids is None:
        one = params.get("id")
        ids = [one] if one else []
    workspace.view["selection"] = [str(value) for value in ids]
    return True


def _focus(workspace, params):
    """Show one card and nothing else, or -- with no id -- everything again.

    View state, not document state: what somebody is looking at right now is not
    part of the workspace they would commit to a repository. It lives here
    rather than in the browser so that an agent asking "what is the user looking
    at" gets the same answer the user would give.
    """
    workspace.view["focus"] = params.get("id")
    return True


def _export(workspace, _params):
    return workspace.document.to_imgql()


def _save(workspace, params):
    path = params.get("path")
    return workspace.save(str(guard.permit(path)) if path else None)


def _set_text(workspace, params):
    """Replace the whole document with this text.

    Editing the file directly is editing the workspace, because they are the
    same thing: the layout is in the file's own comments, so a card moved in the
    browser and a card moved in a text editor are the same edit written the same
    way. Undo covers it like any other change.
    """
    return workspace.set_text(params["text"])


def _library_open(workspace, params):
    return workspace.open(str(guard.permit(params["path"])))


def _library_new_file(workspace, params):
    from . import library

    path = library.new_file(params.get("project"), params.get("name"))
    workspace.open(str(path))
    return str(path)


def _library_new_project(_workspace, params):
    from . import library

    return library.new_project(params["name"])


def _library_move(workspace, params):
    from . import library

    moved = library.move(guard.permit(params["path"]), params.get("project"))
    workspace.follow(params["path"], moved)
    return str(moved)


def _library_rename(workspace, params):
    from . import library

    renamed = library.rename(guard.permit(params["path"]), params["name"])
    workspace.follow(params["path"], renamed)
    return str(renamed)


def _library_rename_project(workspace, params):
    from . import library

    before = library.root() / params["name"]
    after = library.rename_project(params["name"], params["to"])
    workspace.follow_folder(before, library.root() / after)
    return after


def _library_add_folder(workspace, params):
    """Show a folder that already exists as a project.

    With a path, that path. Without one, the system's own folder chooser, off
    this thread -- a modal panel must not freeze the board behind it.
    """
    from . import library, native

    path = params.get("path")
    if path:
        return library.link(guard.permit(path))
    if not guard.may_open_dialogs():
        raise guard.Refused("a system dialogue belongs to whoever is at this machine")

    def ask() -> None:
        chosen = native.choose_folder()
        if chosen:
            # Choosing is what widens the boundary; a client asking never does.
            library.link(guard.approve(chosen))
            workspace.publish()

    threading.Thread(target=ask, name="voxlogica-folder-dialog", daemon=True).start()
    return True


def _library_forget_folder(_workspace, params):
    from . import library

    return library.unlink(params["path"])  # a location we already showed


def _library_delete_project(_workspace, params):
    from . import library

    return library.delete_project(params["name"])


def _library_reveal(_workspace, params):
    from . import native

    if not guard.may_open_dialogs():
        return False
    return native.reveal(guard.permit(params["path"]))


def _library_delete(workspace, params):
    from . import library

    path = guard.permit(params["path"])
    return library.delete(path) and workspace.forget(path)


def _move_to(workspace, params):
    return workspace.move_to(str(guard.permit(params["path"])))


def _rename_workspace(workspace, params):
    return workspace.rename(params["name"])


def _choose_location(workspace, _params):
    """Open the system's own save panel, and move there if it is answered."""
    if not guard.may_open_dialogs():
        raise guard.Refused("a system dialogue belongs to whoever is at this machine")
    workspace.choose_location()
    return True


def _reveal(workspace, _params):
    return workspace.reveal()


def _open(workspace, params):
    return workspace.open(str(guard.permit(params["path"])))


def _action(name, params, required, doc, apply, *, mutates=True):
    return Action(
        name=name, params=params, required=tuple(required), doc=doc, apply=apply,
        mutates=mutates,
    )


def _undo(workspace, _params):
    return workspace.undo()


def _redo(workspace, _params):
    return workspace.redo()


ACTIONS: dict[str, Action] = {
    action.name: action
    for action in (
        _action("board.moveCard", {"id": "string", "x": "int", "y": "int"},
                ("id", "x", "y"), "Move a card to a cell position.", _move),
        _action("board.resizeCard", {"id": "string", "w": "int", "h": "int"},
                ("id", "w", "h"), "Resize a card, in cells.", _resize),
        _action("board.arrange", {"cards": "placements"}, ("cards",),
                "Place several cards at once: [{id, x, y, w, h}]. All or none.",
                _arrange),
        _action("board.addCard",
                {"id": "string", "kind": "string", "x": "int", "y": "int", "w": "int",
                 "h": "int", "page": "int", "node": "string", "title": "string"},
                ("id",), "Add a card to the board.", _add),
        _action("board.duplicateCard",
                {"id": "string", "newId": "string", "x": "int", "y": "int", "page": "int"},
                ("id", "newId"), "Copy a card, its contents included.", _duplicate),
        _action("board.deriveCard",
                {"id": "string", "newId": "string", "kind": "string", "node": "string",
                 "title": "string", "x": "int", "y": "int", "w": "int", "h": "int",
                 "page": "int"},
                ("id", "newId"),
                "Add a card that shows something another card produces.", _derive),
        _action("board.removeCard", {"id": "string"}, ("id",),
                "Remove a card and its contents.", _remove),
        _action("board.setPage", {"id": "string", "page": "int"}, ("id", "page"),
                "Move a card to another page of the board.", _set_page),
        _action("card.setTitle", {"id": "string", "title": "string"}, ("id", "title"),
                "Rename a card.", _set_title),
        _action("card.setSource", {"id": "string", "text": "string"}, ("id", "text"),
                "Replace the program text of a code card.", _set_source),
        _action("card.bindNode", {"id": "string", "node": "string"}, ("id", "node"),
                "Point a result card at a node of the program.", _bind_node),
        _action("card.setKind", {"id": "string", "kind": "string"}, ("id", "kind"),
                "Switch what a card is: code, result or note.", _set_kind),
        _action("card.setViewMode", {"id": "string", "view": "string"}, ("id", "view"),
                "Switch how a result card renders: its state or its content.", _set_view_mode),
        _action("view.goToPage", {"page": "int"}, ("page",),
                "Show a page of the board.", _go_to_page, mutates=False),
        _action("view.setZoom", {"zoom": "number"}, ("zoom",),
                "Scale the board.", _set_zoom, mutates=False),
        _action("view.select", {"id": "string", "ids": "placements"}, (),
                "Select nothing, one card (id) or several (ids).", _select, mutates=False),
        _action("view.focus", {"id": "string"}, (),
                "Show one card alone, or -- with no id -- the whole board.", _focus, mutates=False),
        _action("library.open", {"path": "string"}, ("path",),
                "Open a file from the library; the pane shows one at a time.",
                _library_open, mutates=False),
        _action("library.newFile", {"project": "string", "name": "string"}, (),
                "Make a file, in a project or loose at the top of the library.",
                _library_new_file, mutates=False),
        _action("library.newProject", {"name": "string"}, ("name",),
                "Make a project, which is a folder.", _library_new_project, mutates=False),
        _action("library.moveFile", {"path": "string", "project": "string"}, ("path",),
                "Move a file into a project, or out to the top of the library.",
                _library_move, mutates=False),
        _action("library.renameFile", {"path": "string", "name": "string"}, ("path", "name"),
                "Rename a file.", _library_rename, mutates=False),
        _action("library.renameProject", {"name": "string", "to": "string"}, ("name", "to"),
                "Rename a project.", _library_rename_project, mutates=False),
        _action("library.addFolder", {"path": "string"}, (),
                "Show an existing folder as a project; nothing is moved or copied.",
                _library_add_folder, mutates=False),
        _action("library.forgetFolder", {"path": "string"}, ("path",),
                "Stop showing a linked folder. The folder itself is untouched.",
                _library_forget_folder, mutates=False),
        _action("library.reveal", {"path": "string"}, ("path",),
                "Show a file or project in the file manager.", _library_reveal, mutates=False),
        _action("library.deleteProject", {"name": "string"}, ("name",),
                "Remove an empty project folder. Refused if it still holds files.",
                _library_delete_project, mutates=False),
        _action("library.deleteFile", {"path": "string"}, ("path",),
                "Delete a file from the library.", _library_delete, mutates=False),
        _action("workspace.open", {"path": "string"}, ("path",),
                "Open an .imgql file as the workspace document.", _open),
        _action("workspace.undo", {}, (), "Undo the last change to the document.",
                _undo, mutates=False),
        _action("workspace.redo", {}, (), "Redo the change that was just undone.",
                _redo, mutates=False),
        _action("workspace.setText", {"text": "string"}, ("text",),
                "Replace the whole document with this .imgql text.", _set_text),
        _action("workspace.export", {}, (),
                "The document as .imgql text, exactly as it would be saved.", _export, mutates=False),
        _action("workspace.moveTo", {"path": "string"}, ("path",),
                "Move this workspace to a path, taking the old file away.",
                _move_to, mutates=False),
        _action("workspace.rename", {"name": "string"}, ("name",),
                "Rename this workspace, which renames its folder.",
                _rename_workspace, mutates=False),
        _action("workspace.chooseLocation", {}, (),
                "Ask the system where this workspace should live, and move it there.",
                _choose_location, mutates=False),
        _action("workspace.reveal", {}, (),
                "Show this workspace's file in the file manager.", _reveal, mutates=False),
        _action("workspace.save", {"path": "string"}, (),
                "Write the document back to disk.", _save, mutates=False),
    )
}


def manifest() -> list[dict[str, Any]]:
    """The vocabulary as plain data, for MCP tool schemas and for tests."""
    return [
        {
            "name": action.name,
            "doc": action.doc,
            "params": dict(action.params),
            "required": list(action.required),
        }
        for action in ACTIONS.values()
    ]


def json_schema(action: Action) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": JSON_TYPES[kind]} for name, kind in action.params.items()
        },
        "required": list(action.required),
    }
