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

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import guard
from .document import parse as parse_document

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
    return workspace.document.arrange(params.get("cards") or [])


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


def _copy_cards(workspace, params):
    """Those cards as .imgql text -- which is what goes on the clipboard.

    The cut buffer is the file format. Pasted into a text editor it is readable
    program text; pasted back here it is cards again; and there is no second
    format that can drift from this one.
    """
    return workspace.document.fragment([str(i) for i in params.get("ids") or []])


def _cut_cards(workspace, params):
    ids = [str(i) for i in params.get("ids") or []]
    text = workspace.document.fragment(ids)
    for card_id in ids:
        workspace.document.remove_card(card_id)
    return text


def _paste_cards(workspace, params):
    return workspace.document.import_fragment(
        params["text"],
        page=params.get("page"),
        x=params.get("x"),
        y=params.get("y"),
    )


def _measured(workspace, params):
    """Write down what a self-sizing card came to.

    The board measures a card against its content; only the browser can. But
    the *document* is what keeps cards from sharing a cell, and it cannot keep
    that about a card whose footprint it does not know -- which is how anything
    could be placed on top of an auto card. So the measurement comes back here.

    `auto` stays set: it records where the size came from, and the card goes on
    re-measuring when its content changes. Refused if the size would overlap
    somebody, which leaves the card drawn at its measurement and stored at its
    last agreed one -- a smaller lie than a board whose rules do not hold.
    """
    return workspace.document.measured(params["id"], int(params["w"]), int(params["h"]))


def _untangle(workspace, _params):
    return workspace.document.untangle()


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


def _set_layer_style(workspace, params):
    """How one layer of a stack looks. Never what it is.

    A slider moves at sixty frames a second and this must stay free, which is
    exactly why the style is a directive and not part of the expression: the
    expression is the cache key, and a hash that changes recomputes a volume.
    """
    return workspace.document.set_layer_style(
        params["id"],
        int(params["at"]),
        colormap=params.get("colormap"),
        opacity=params.get("opacity"),
        on=params.get("on"),
    )


#: What to call a layer that has just become a card. Its own leading name is
#: what the author would have called it.
_LEADING_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _move_layer(workspace, params):
    """Which layer is in front. The order of the array is the order it draws in."""
    return workspace.document.move_layer(
        params["id"], int(params["at"]), int(params["to"])
    )


def _merge_card(workspace, params):
    """Drop one card onto another: what it drew is now a layer of what it landed on.

    The card that was dropped stops existing, which is what the gesture looks
    like -- it became a row. Its style travels with it, so a mask that was red
    is still red one line further down.
    """
    document = workspace.document
    taken = document.layers_of(params["from"])
    if not taken or params["from"] == params["id"]:
        return False
    if not document.add_layers(params["id"], taken):
        return False
    document.remove_card(params["from"])
    return True


def _split_layer(workspace, params):
    """Take a layer out of a stack and give it a card of its own.

    The other half of the merge, and it has to be the other half exactly:
    dropping a card in and dragging its row back out must leave the program
    where it started, or the gesture is not reversible and nobody trusts it.
    """
    document = workspace.document
    lifted = document.take_layer(params["id"], int(params["at"]))
    if lifted is None:
        return False
    expression, style = lifted
    new_id = params.get("newId") or document.next_id()
    label = _LEADING_NAME.search(expression)
    made = document.add_card(
        new_id,
        "print",
        x=params.get("x"),
        y=params.get("y"),
        page=params.get("page"),
        w=params.get("w"),
        h=params.get("h"),
    )
    if not made:
        return False
    document.set_source(new_id, f'print "{label.group(0) if label else new_id}" {expression}\n')
    # Position zero of a card of its own, wearing the colour it had in the stack.
    document.set_layer_style(new_id, 0, **style)
    return new_id


def _set_view_mode(workspace, params):
    return workspace.document.set_attr(params["id"], "view", params["view"])


def _go_to_page(workspace, params):
    workspace.view["page"] = params["page"]
    return True


#: The three distances a card can be looked at from. Not modes: `source` and
#: `value` are the ends of one movement and `both` is the middle.
LENSES = ("source", "both", "value")


def _show(workspace, params):
    """Board or document -- the two distances Tab swaps between.

    View state, and on the server for the reason all of it is: an agent asked
    what the user is looking at has to be able to answer, and to put them back
    where they were.
    """
    showing = params["showing"]
    if showing not in ("board", "document"):
        raise ValueError("showing must be 'board' or 'document'")
    workspace.view["showing"] = showing
    return True


def _set_lens(workspace, params):
    """Set the board's lens.

    View state, like zoom and page: how far back somebody is standing is not a
    property of the program, and a diff that changed because they leaned in
    would be a diff nobody wants to review. A card may override it; the board
    setting is what the rest follow, because a board where twenty cards each sit
    in a mode somebody set once is a board you cannot read at a glance.
    """
    lens = params["lens"]
    if lens not in LENSES:
        raise ValueError(f"lens must be one of {', '.join(LENSES)}")
    workspace.view["lens"] = lens
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


def _tidy(workspace, _params):
    """Put the cards in dependency order now, rather than at the next write."""
    return workspace.document.tidy()


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


def _label(workspace, params, add: bool):
    """Add or remove one label, in the file that carries it.

    Written through the document, not by patching the line: parsing and writing
    a `//@board` directive is something `document.py` already does losslessly,
    and a second writer of that syntax would be a second chance to get the
    quoting wrong.

    The open file is edited in place so the board redraws; any other file is
    read, changed and written -- labelling something is not opening it.
    """
    from . import labels as labelling

    path = guard.permit(params["path"])
    label = labelling.clean(params["label"])
    if not label:
        return False

    if workspace.path is not None and Path(path) == workspace.path:
        document = workspace.document
        current = labelling.parse(document.to_imgql())
        wanted = _with(current, label, add)
        if wanted == current:
            return False
        document.set_board(labels=",".join(wanted) or None)
    else:
        text = Path(path).read_text()
        current = labelling.parse(text)
        wanted = _with(current, label, add)
        if wanted == current:
            return False
        document = parse_document(text)
        document.set_board(labels=",".join(wanted) or None)
        Path(path).write_text(document.to_imgql())
        labelling.forget(Path(path))
    return True


def _with(current: list[str], label: str, add: bool) -> list[str]:
    """The label list this operation wants. Order is the order they were given,
    because a label list that reshuffled itself would put noise in a diff."""
    if add:
        return current if label in current else [*current, label]
    return [item for item in current if item != label]


def _add_label(workspace, params):
    return _label(workspace, params, add=True)


def _remove_label(workspace, params):
    return _label(workspace, params, add=False)


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


def _library_copy(_workspace, params):
    from . import library

    return str(library.copy(guard.permit(params["path"]), params.get("project")))


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


def _library_paste_cards(workspace, params):
    """Put the cards in this .imgql text into a file that is not on screen.

    This is what a card dragged onto a row in the sidebar lands as. The text is
    the same text `board.copyCards` hands the clipboard, and it is merged by the
    same `Document.import_fragment` -- so ids are minted fresh and bindings that
    would collide are renamed with their references, exactly as they are on the
    board. A second implementation of "add these cards to a file" would be a
    second set of rules for what happens when two cards claim one name, and the
    two would disagree the first time either changed.

    A drop on the file that *is* open is not a special case worth a special
    path: it is a paste, so it goes through the ordinary one and is undoable
    like any other paste. Writing that file behind the workspace's back would
    lose it at the next autosave.
    """
    from pathlib import Path as _Path

    from . import document as doc

    path = guard.permit(params["path"])
    if workspace.path is not None and _Path(workspace.path).resolve() == path:
        return workspace.apply("board.pasteCards", {"text": params["text"]})
    target = doc.parse(path.read_text() if path.exists() else "")
    made = target.import_fragment(params["text"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(target.to_imgql())
    return made


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


def _node_id(workspace, params):
    """A node, named the way a person names one.

    A `let` name is what somebody types and what a card carries, so that is what
    these actions take. A hash is accepted too and passes straight through --
    an agent that already has one should not have to invent a name for it.
    """
    named = params["node"]
    results = getattr(workspace, "results", None)
    if results is None:
        raise ValueError("this workspace has no results")
    return results.resolve(named) or named


def _set_focus(workspace, params):
    """Point a card at one of its own bindings.

    Clearing it is not "no focus": it is *back to the default*, which is the
    last binding the fragment declares. A card about nothing would be a card
    with no reason to have a Run button.
    """
    return workspace.document.set_attr(params["id"], "focus", params.get("focus") or None)


def _output_from(workspace, params, operation):
    """Declare what a card is about as an output of the program.

    A card shows you a value; it cannot put one on disk. `print` and `save` are
    what a program says it produces, so asking for either writes the directive
    into the text -- where a reader can see it, a diff can show it, and a
    headless run can perform it. A button that wrote a file directly would be
    an effect with no record.
    """
    card = next((entry for entry in workspace.document.cards
                 if entry.get("id") == params["id"]), None)
    if card is None:
        raise ValueError(f"no card {params['id']!r}")
    expression = card.get("focus") or card.get("node")
    if not expression:
        raise ValueError("this card is not about anything yet")
    label = params.get("label") or expression
    return workspace.document.add_output(operation, label, expression)


def _save_this(workspace, params):
    return _output_from(workspace, params, "save")


def _print_this(workspace, params):
    return _output_from(workspace, params, "print")


def _run_card(workspace, params):
    """Ask for everything a card is about.

    Everything it *defines*, not only what it focuses on: a fragment's earlier
    bindings are the working that leads to its last one, and a card whose middle
    steps stayed `unknown` while its answer went `done` would be showing a
    computation that did not happen the way it happened.

    Dependencies are not listed here and never need to be. They are other
    cards' bindings, which are the same hashes, and the engine reaches them
    because the goals it was given need them.
    """
    card = next((entry for entry in workspace.document.cards
                 if entry.get("id") == params["id"]), None)
    if card is None:
        raise ValueError(f"no card {params['id']!r}")
    if workspace.compute is None or workspace.results is None:
        raise ValueError("this workspace cannot run anything")

    source = workspace.document.to_imgql()
    bindings = workspace.results.bindings
    # A card bound to a node by hash asks for that; a code card asks for the
    # names it declares. Both end as node ids, which is all a demand is.
    wanted = [bindings[name] for name in _defined_by(card) if name in bindings]
    if card.get("node"):
        wanted.append(bindings.get(card["node"], card["node"]))
    return workspace.compute.demand(source, wanted)


def _defined_by(card):
    """The names a card's own text declares, asked of the real parser."""
    from . import analysis

    uses = analysis.analyse(card.get("source") or "")
    return sorted(uses.defines) if uses is not None else []


def _hash_of(workspace, params):
    """What a selection in the editor is, and whether it is already computed.

    Answered on the server because there is one hasher and it is the reducer.
    A JavaScript reimplementation would drift and then answer cache questions
    wrongly and *silently*, which is worse than not answering them.
    """
    from .results import hash_of

    node = hash_of(workspace.document.to_imgql(), params["expression"])
    if node is None:
        return None
    state = workspace.results.state_of(node) if workspace.results else {"hash": node}
    return state


def _results_get(workspace, params):
    return workspace.results.state_of(_node_id(workspace, params))


def _results_wait(workspace, params):
    """The awaited half of R6, for an agent.

    The alternative is an agent polling `results.get`, which is the same
    question asked repeatedly with the answer arriving late by construction.
    """
    return workspace.results.wait(
        _node_id(workspace, params),
        state=params.get("state") or "done",
        timeout=float(params.get("timeout") or 60.0),
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
        _action("board.copyCards", {"ids": "placements"}, ("ids",),
                "Those cards as .imgql text, for the clipboard.", _copy_cards, mutates=False),
        _action("board.cutCards", {"ids": "placements"}, ("ids",),
                "Those cards as .imgql text, and remove them.", _cut_cards),
        _action("board.pasteCards",
                {"text": "string", "page": "int", "x": "int", "y": "int"}, ("text",),
                "Add the cards in this .imgql text, renaming what would collide.",
                _paste_cards),
        _action("board.removeCard", {"id": "string"}, ("id",),
                "Remove a card and its contents.", _remove),
        _action("board.measured", {"id": "string", "w": "int", "h": "int"},
                ("id", "w", "h"),
                "Record the size an auto card measured itself at, so the "
                "document knows what every card covers.", _measured),
        _action("board.untangle", {}, (),
                "Move cards apart until none share a cell. For a document that "
                "arrived overlapping.", _untangle),
        _action("board.setPage", {"id": "string", "page": "int"}, ("id", "page"),
                "Move a card to another page of the board.", _set_page),
        _action("card.setTitle", {"id": "string", "title": "string"}, ("id", "title"),
                "Rename a card.", _set_title),
        _action("card.setSource", {"id": "string", "text": "string"}, ("id", "text"),
                "Replace the program text of a code card.", _set_source),
        _action("card.setFocus", {"id": "string", "focus": "string"}, ("id",),
                "Choose which of a card's bindings it is about; with no focus, "
                "the last one it declares.", _set_focus),
        _action("card.bindNode", {"id": "string", "node": "string"}, ("id", "node"),
                "Point a result card at a node of the program.", _bind_node),
        _action("card.setKind", {"id": "string", "kind": "string"}, ("id", "kind"),
                "Switch what a card is: code, result or note.", _set_kind),
        _action("card.setViewMode", {"id": "string", "view": "string"}, ("id", "view"),
                "Switch how a result card renders: its state or its content.", _set_view_mode),
        _action("card.setLayerStyle",
                {"id": "string", "at": "int", "colormap": "string", "opacity": "number",
                 "on": "bool"},
                ("id", "at"),
                "How one layer of a stack looks: colormap, opacity, on or off. "
                "Appearance only -- nothing recomputes.", _set_layer_style),
        _action("card.moveLayer", {"id": "string", "at": "int", "to": "int"},
                ("id", "at", "to"),
                "Reorder a stack: which layer draws in front of which.", _move_layer),
        _action("card.mergeCard", {"id": "string", "from": "string"}, ("id", "from"),
                "Lay what one card draws on top of another. The first stops "
                "existing: it became a layer.", _merge_card),
        _action("card.splitLayer",
                {"id": "string", "at": "int", "newId": "string", "x": "int", "y": "int",
                 "w": "int", "h": "int", "page": "int"},
                ("id", "at"),
                "Take a layer out of a stack into a card of its own.", _split_layer),
        _action("card.saveThis", {"id": "string", "label": "string"}, ("id",),
                "Write a `save` for what this card is about into the program, "
                "as its own card.", _save_this),
        _action("card.printThis", {"id": "string", "label": "string"}, ("id",),
                "Write a `print` for what this card is about into the program, "
                "as its own card.", _print_this),
        _action("card.run", {"id": "string"}, ("id",),
                "Compute what a card is about. Its dependencies follow, and "
                "every card showing one of them updates as it happens.",
                _run_card, mutates=False),
        _action("results.hashOf", {"expression": "string"}, ("expression",),
                "The node a sub-expression of this document would compile to, "
                "and what is known about it. Null when it is not an expression.",
                _hash_of, mutates=False),
        _action("results.get", {"node": "string"}, ("node",),
                "What is known about a node right now: its state, and its value "
                "when there is a small one.", _results_get, mutates=False),
        _action("results.wait", {"node": "string", "state": "string", "timeout": "number"},
                ("node",),
                "Block until a node reaches a state (default done), then return "
                "it. Bounded: says so rather than hanging.", _results_wait, mutates=False),
        _action("view.goToPage", {"page": "int"}, ("page",),
                "Show a page of the board.", _go_to_page, mutates=False),
        _action("view.show", {"showing": "string"}, ("showing",),
                "Look at the board, or at the document the board is drawn from.",
                _show, mutates=False),
        _action("view.setLens", {"lens": "string"}, ("lens",),
                "How far back the board stands from its cards: source, both or "
                "value.", _set_lens, mutates=False),
        _action("view.setZoom", {"zoom": "number"}, ("zoom",),
                "Scale the board.", _set_zoom, mutates=False),
        _action("view.select", {"id": "string", "ids": "placements"}, (),
                "Select nothing, one card (id) or several (ids).", _select, mutates=False),
        _action("view.focus", {"id": "string"}, (),
                "Show one card alone, or -- with no id -- the whole board.", _focus, mutates=False),
        _action("library.addLabel", {"path": "string", "label": "string"},
                ("path", "label"),
                "Give a file a label. It is written into the file itself, so it "
                "travels with it.", _add_label),
        _action("library.removeLabel", {"path": "string", "label": "string"},
                ("path", "label"), "Take a label off a file.", _remove_label),
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
        _action("library.copyFile", {"path": "string", "project": "string"}, ("path",),
                "Copy a file into a project, or to the top of the library.",
                _library_copy, mutates=False),
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
        _action("library.pasteCards", {"path": "string", "text": "string"},
                ("path", "text"),
                "Add the cards in this .imgql text to a file of the library, "
                "renaming what would collide.", _library_paste_cards, mutates=False),
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
        _action("workspace.tidy", {}, (),
                "Order the cards so every name is defined before it is used.", _tidy),
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
