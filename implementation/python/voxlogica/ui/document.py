"""The workspace document: a bento layout that is also a valid .imgql program.

A document is an .imgql file plus the layout of the cards it is shown in, and the
layout is carried in comments -- so the file runs, diffs and commits like any
other source. There is no sidecar JSON, because a sidecar is a second file to
keep in step with the first and a reader cannot see it.

The whole design serves one property, which the tests state as an equality:

    to_imgql(parse(text)) == text        byte for byte, for an untouched document

That is why nothing here is a "representation" of the program. The text is kept
verbatim, in segments, and export is concatenation. A formatter, a re-quoter or a
normaliser anywhere in this file would turn losslessness from a property into a
hope.

See doc/dev/ui-workspace.md section 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# A directive is a comment first and metadata second: `//` is a comment in
# .imgql, and the `@` makes the marker specific enough to grep for and unlikely
# to collide with prose. Anything else on the line is attributes.
_DIRECTIVE = re.compile(r"^//@(board|card)\b[ \t]*(.*)$")
#: A quoted value may contain quotes, escaped with a backslash the way every
#: other string in this project escapes them. The alternation is ordered so a
#: quoted value is tried first; a bare value is anything without whitespace.
_ATTR = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=(?:"((?:[^"\\]|\\.)*)"|(\S+))')

#: Written in this order when a directive is regenerated, so that moving one card
#: produces a one-line diff instead of a reshuffled file. Keys not listed keep
#: their original relative order and come last -- an attribute this build does
#: not understand still survives a round trip.
_KEY_ORDER = (
    "id",
    "title",
    "kind",
    "x",
    "y",
    "w",
    "h",
    "minW",
    "minH",
    "maxW",
    "maxH",
    "aspect",
    "auto",
    "page",
    "node",
    "focus",
    "index",
    "view",
    "style",
    "from",
    "cols",
    "rows",
    "labels",
)

_GEOMETRY = ("x", "y", "w", "h", "page")

#: A note's text is prose, and prose is not a program. It is stored commented so
#: that the invariant this whole format exists for -- *the document is always a
#: valid .imgql program* -- survives somebody writing a sentence in a card. The
#: UI never sees the prefix: it is added on the way in and taken off on the way
#: out, in one place, here.
_NOTE_PREFIX = "// "


def _uncomment(text: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("//"):
            indent = line[: len(line) - len(stripped)]
            body = stripped[2:]
            lines.append(indent + (body[1:] if body.startswith(" ") else body))
        else:
            lines.append(line)
    return "".join(lines)


def _comment(text: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "" or line.lstrip().startswith("//"):
            out.append(line)
        else:
            out.append(_NOTE_PREFIX + line)
    return "".join(out)


#: Always written quoted, whatever they contain. A title is prose -- somebody
#: will type a space into it within the hour -- and a field that is only
#: sometimes quoted teaches every reader of the file the wrong rule.
_ALWAYS_QUOTED = ("title", "labels", "style")


#: How one layer looks: a colormap, optionally an opacity, optionally switched
#: off -- `gray`, `blue@0.35`, `red@0.45!off`.
_STYLE = re.compile(r"^([A-Za-z_][\w-]*)(?:@([0-9]*\.?[0-9]+))?(!off)?$")

#: A layer whose style this build could not read. Not dropped: a style list is
#: positional, so dropping entry two would silently repaint entry three.
_PLAIN: dict[str, Any] = {"colormap": None, "opacity": 1.0, "on": True}


def styles(value: str) -> list[dict[str, Any]]:
    """`"gray@1.00, blue@0.35!off"` as one entry per layer, in drawing order.

    Appearance, and only appearance. It lives in the directive rather than in the
    expression because *the expression is the cache key*: put opacity in the
    program and moving a slider changes a hash, and changing a hash recomputes
    three hundred megabytes of volume because somebody dragged a cursor.

    The nth entry styles the nth element of the card's array, so the count of
    entries is load-bearing and a value that does not parse still occupies its
    place. The raw attribute is what gets written back regardless, so nothing a
    later build understands is lost here.
    """
    out: list[dict[str, Any]] = []
    for piece in value.split(","):
        piece = piece.strip()
        if not piece:
            continue
        match = _STYLE.match(piece)
        if match is None:
            out.append(dict(_PLAIN))
            continue
        colormap, opacity, off = match.groups()
        out.append({
            "colormap": colormap,
            "opacity": 1.0 if opacity is None else float(opacity),
            "on": off is None,
        })
    return out


def style_text(layers: list[dict[str, Any]]) -> str:
    """The other direction, for whoever moves a slider."""
    written = []
    for layer in layers:
        text = f"{layer.get('colormap') or 'gray'}@{float(layer.get('opacity', 1.0)):.2f}"
        if layer.get("on") is False:
            text += "!off"
        written.append(text)
    return ", ".join(written)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


#: What a code card defines. Textual and provisional -- the engine's own binder
#: knows better -- but it is what the UI can see today, and it is the same rule
#: the "new result from this" menu uses.
_BINDING = re.compile(r"^[ \t]*let[ \t]+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def bindings_in(source: str) -> list[str]:
    return _BINDING.findall(source or "")


def _unterminated(parts: list[str]) -> bool:
    """Whether what has been written so far stops mid-line.

    Asked of the last part that *has* any characters: an empty body contributes
    nothing, and looking at it rather than through it inserts a blank line after
    every directive that has no text under it.
    """
    for part in reversed(parts):
        if part:
            return not part.endswith("\n")
    return False


def _focus_of(card: dict[str, Any]) -> str | None:
    """Which binding a card is *about*.

    A card holds a fragment, and a fragment may declare several names. The one
    it is about is the last it declares -- not the last line of a file, which is
    arbitrary and moves when somebody appends to it, but the last of a fragment
    whose boundary was drawn by hand. A fragment reads as scaffolding building
    toward its final name: the earlier bindings are the working, the last one is
    the answer.

    A stated `focus=` always wins, and it is answered here rather than in the
    browser so that a person and an agent are looking at the same card. Kept in
    one place for the same reason the title and the id are: two implementations
    of "what is this card about" would disagree on the day one was updated.

    A result card has no fragment of its own; what it is about is the node it is
    bound to.
    """
    stated = card.get("focus")
    if stated:
        return stated
    declared = bindings_in(card.get("source", ""))
    if declared:
        return declared[-1]
    return card.get("node")


def _free_name(name: str, taken: set[str]) -> str:
    n = 2
    while f"{name}{n}" in taken:
        n += 1
    return f"{name}{n}"


def _quote(key: str, value: str) -> str:
    """Quote when the value could not be read back as one token, or always."""
    if key in _ALWAYS_QUOTED or value == "" or re.search(r'[\s"\\]', value):
        return f'"{_escape(value)}"'
    return value


@dataclass
class Directive:
    """One `//@board` or `//@card` line."""

    kind: str
    attrs: dict[str, str]
    raw: str
    #: Set when an attribute changed and `raw` can no longer be trusted. Until
    #: then the original line is what gets written back, comments, spacing and
    #: unknown keys included.
    rewritten: bool = False

    def render(self) -> str:
        if not self.rewritten:
            return self.raw
        known = [key for key in _KEY_ORDER if key in self.attrs]
        rest = [key for key in self.attrs if key not in _KEY_ORDER]
        pairs = " ".join(f"{key}={_quote(key, self.attrs[key])}" for key in known + rest)
        return f"//@{self.kind} {pairs}".rstrip()

    def set(self, key: str, value: Any) -> None:
        text = "" if value is None else str(value)
        if self.attrs.get(key) == text:
            return
        if value is None:
            self.attrs.pop(key, None)
        else:
            self.attrs[key] = text
        self.rewritten = True

    def int_or(self, key: str, default: int | None) -> int | None:
        try:
            return int(self.attrs[key])
        except (KeyError, ValueError):
            return default


@dataclass
class Segment:
    """A directive and the text under it, both exactly as they were read.

    `directive is None` only for the preamble -- whatever came before the first
    directive in the file, which is where a plain program lives.
    """

    directive: Directive | None
    body: str = ""


@dataclass
class Document:
    """A parsed workspace file. `source` is the bytes it came from."""

    segments: list[Segment] = field(default_factory=list)
    source: str = ""
    #: Did the file carry directives? A file that did not is shown as one code
    #: card, and is written back untouched unless the user arranges something.
    annotated: bool = False
    #: Has anything changed since it was read?
    dirty: bool = False

    # ---------------------------------------------------------------- reading

    @property
    def board(self) -> dict[str, int]:
        for segment in self.segments:
            if segment.directive and segment.directive.kind == "board":
                return {
                    "cols": segment.directive.int_or("cols", 12) or 12,
                    "rows": segment.directive.int_or("rows", 8) or 8,
                }
        return {"cols": 12, "rows": 8}

    @property
    def cards(self) -> list[dict[str, Any]]:
        """The cards, in file order, as the shape the board and MCP both read.

        A file with no directives yields exactly one card holding the whole
        program: that is the degenerate case working, not a special case bolted
        on. It has no width or height, so the board sizes it to its content.
        """
        if not self.annotated:
            return self._unarranged()

        cards: list[dict[str, Any]] = []
        for index, segment in enumerate(self.segments):
            directive = segment.directive
            if directive is None or directive.kind != "card":
                continue
            attrs = directive.attrs
            identity = attrs.get("id") or f"card{index}"
            card: dict[str, Any] = {
                #: The reference. Stable, never shown, and what one card names
                #: another by -- a card that is a view of another finds it here.
                "id": identity,
                #: The name. Prose, editable, and free to collide with another
                #: card's: it is not identity, which is the whole reason the two
                #: are separate fields. Files written before the split have only
                #: an id, and it reads as a name because that is what it was.
                "title": attrs.get("title", identity),
                "kind": attrs.get("kind", "code"),
                "x": directive.int_or("x", 0),
                "y": directive.int_or("y", 0),
                "page": directive.int_or("page", 0),
            }
            for key in ("w", "h", "minW", "minH", "maxW", "maxH"):
                value = directive.int_or(key, None)
                if value is not None:
                    card[key] = value
            for key in ("node", "view", "from", "focus", "index"):
                if key in attrs:
                    card[key] = attrs[key]
            # A card that names an index is a card somebody can walk: that
            # attribute is the whole of what used to be `kind=selector`.
            if "style" in attrs:
                card["style"] = styles(attrs["style"])
            if "aspect" in attrs:
                try:
                    card["aspect"] = float(attrs["aspect"])
                except ValueError:
                    pass
            # No w/h in the file means the card was never sized by hand, which is
            # exactly what the board calls `auto`.
            card["auto"] = attrs.get("auto", "true" if "w" not in attrs else "false") == "true"
            # Whatever is under the directive belongs to the card, whatever the
            # card is. A note's prose is stored commented so the file still runs,
            # and is handed to the UI without the prefix -- nobody types `//` to
            # write a sentence.
            card["source"] = (
                _uncomment(segment.body) if card["kind"] == "note" else segment.body
            )
            # A print or save card is *about* what its own line prints. In an
            # unarranged file that comes from `outputs()`; here the card's
            # source is the directive's own `print` line, so it is the same
            # question asked of one line instead of the whole file. Without
            # this, laying a board out is what un-binds every output card on it
            # -- the card reads "not bound to a node yet" about a line that
            # plainly says what it is about, and its play button computes
            # nothing.
            if card["kind"] in ("print", "save") and "node" not in card:
                from . import analysis

                declared = analysis.outputs(card["source"])
                if declared:
                    # A stack: one output, several pictures. Each element is a
                    # node of its own, so the card carries them all and the
                    # workspace asks the reducer for a hash per element. The
                    # single `node` below stays what it was -- the whole array --
                    # which is what Run computes and what the state is about.
                    if declared[0].parts:
                        card["parts"] = list(declared[0].parts)
                    card["node"] = declared[0].binding or declared[0].expression
                    if not declared[0].binding:
                        card["expression"] = declared[0].expression
                    if declared[0].label:
                        card.setdefault("title", declared[0].label)
                        if card["title"] == identity:
                            card["title"] = declared[0].label
            card["focus"] = _focus_of(card)
            cards.append(card)
        return cards

    def _unarranged(self) -> list[dict[str, Any]]:
        """A board for a file nobody has arranged: the program, and its outputs.

        A `print` and a `save` are what the program *says* it produces, with a
        name its author chose, so they are what a board should show when there
        is no layout to read. They are kept apart -- a print is a value shown, a
        save is an effect with a destination -- because a card that conflated
        them would have to explain which one it was every time.

        Nothing else here becomes a card yet. Text carrying no directive stays
        in the file and is reached through the document view; the stronger rule
        this is half of, *nothing in the program may be invisible on the board*,
        is worth revisiting once real files have been opened in anger.

        **Derived, not written.** Opening a file must not modify it: these cards
        exist in this list and nowhere else until the first edit, which is what
        turns a plain file into an annotated one (see `_annotate`). Somebody who
        opens a program to look at it and closes it again finds their file
        exactly as they left it, byte for byte.
        """
        from . import analysis

        declared = analysis.outputs(self.source)
        program = {
            "id": "program",
            "kind": "code",
            "title": "Program",
            "x": 0,
            "y": 0,
            "page": 0,
            "source": self.source,
        }
        if not declared:
            # The degenerate case, unchanged: one card, sized to its content.
            return [{**program, "auto": True, "focus": _focus_of(program)}]

        # With something beside it, the program takes a stated column instead of
        # measuring itself: an auto card's width is not known until it is drawn,
        # and a layout that cannot say where its second column starts would put
        # one card on top of another the first time a program was wide.
        cards: list[dict[str, Any]] = [
            {**program, "w": 7, "h": 9, "auto": False, "focus": _focus_of(program)}
        ]
        for index, output in enumerate(declared):
            card: dict[str, Any] = {
                "id": f"{output.operation}{index + 1}",
                "kind": output.operation,
                "title": output.label,
                "x": 8,
                "y": index * 3,
                "w": 5,
                "h": 3,
                "page": 0,
                "auto": False,
            }
            #: What the card is about. A bare name is the common shape and the
            #: tidy one -- it reads as a name everywhere it appears. Anything
            #: larger is bound to the expression *as written*, which the
            #: workspace resolves to a hash through the same reducer.
            #:
            #: Leaving those unbound is what made `print "x" volume(gt(1))`
            #: a card with a play button that did nothing: no node, nothing to
            #: compute, and no state to show. A print is a node in the DAG
            #: whether or not somebody gave it a name first.
            card["node"] = output.binding or output.expression
            card["focus"] = card["node"]
            if not output.binding:
                card["expression"] = output.expression
            cards.append(card)
        return cards

    def find(self, card_id: str) -> Segment | None:
        for segment in self.segments:
            directive = segment.directive
            if directive and directive.kind == "card" and directive.attrs.get("id") == card_id:
                return segment
        return None

    # ---------------------------------------------------------------- writing

    def to_imgql(self) -> str:
        """The file. Byte-identical to the source until something is changed."""
        if not self.dirty:
            return self.source
        if not self.annotated:
            # A plain program that has now been arranged: annotate it, once.
            self._annotate()
        parts: list[str] = []
        for segment in self.segments:
            if segment.directive is not None:
                # A directive is only a directive at the start of a line: the
                # pattern is anchored, and it has to be, or a `//@card` inside a
                # string would restructure somebody's document. So a body that
                # does not end in a newline would glue the *next* directive onto
                # its last line, where the next parse reads it as ordinary text
                # -- and the card it described, plus every card after it, is
                # gone. A textarea hands back exactly such a body whenever the
                # user does not press Enter last, which is most of the time.
                if _unterminated(parts):
                    parts.append("\n")
                parts.append(segment.directive.render())
                parts.append("\n")
            parts.append(segment.body)
        return "".join(parts)

    def set_board(self, **attrs: Any) -> bool:
        """Set attributes on the `//@board` line, making it if there is none.

        A file with no directives gains them here, exactly as it does when a
        card is first moved: labelling a plain program is an edit like any
        other, and the format's whole promise is that adding a comment leaves it
        a valid program.
        """
        if not self.annotated:
            self._annotate()
        for segment in self.segments:
            if segment.directive is not None and segment.directive.kind == "board":
                for key, value in attrs.items():
                    segment.directive.set(key, value)
                self.dirty = True
                return True
        return False

    def _annotate(self) -> None:
        board = Directive("board", {"cols": "12", "rows": "8"}, "", rewritten=True)
        card = Directive(
            "card",
            {"id": "program", "title": "Program", "kind": "code", "x": "0", "y": "0"},
            "",
            rewritten=True,
        )
        self.segments = [Segment(board, ""), Segment(card, self.source)]
        self.annotated = True

    def _writable(self, card_id: str) -> Segment | None:
        """The segment for a card, annotating the file if that is what it takes.

        A file with no directives is shown as one card called `program`, and
        that card is as real as any other: it can be moved, renamed, edited. The
        first such change is what turns the plain file into an annotated one --
        which is why every mutation goes through here rather than each deciding
        for itself. `set_attr` did not, and renaming that card silently did
        nothing at all.
        """
        segment = self.find(card_id)
        if segment is not None and segment.directive is not None:
            return segment
        if not self.annotated and card_id == "program":
            self._annotate()
            self.dirty = True
            segment = self.find(card_id)
            if segment is not None and segment.directive is not None:
                return segment
        return None

    def place(self, card_id: str, *, _checked: bool = True, **geometry: int) -> bool:
        """Move or resize a card. False if there is no such card, or no room.

        **Cards never share a cell.** Not "are discouraged from": a placement
        that would overlap is refused here, at the one point geometry is
        written, so no gesture and no agent can produce a board the drag
        arithmetic cannot reason about. The board refuses these visually too --
        a drop onto occupied cells snaps back -- but a rule enforced only in a
        component is a rule an MCP client walks straight past.

        A card the document has not sized is left out of the question: unknown
        is not the same as small, and treating it as one cell is what let other
        cards be grown over it.
        """
        segment = self._writable(card_id)
        if segment is None:
            return False

        # `_checked=False` only from `arrange`, which has already validated the
        # arrangement as a whole -- see there for why a step-by-step check is
        # the wrong question.
        if _checked and not self._room_for(card_id, geometry):
            return False

        for key, value in geometry.items():
            if key not in _GEOMETRY and key not in ("w", "h"):
                raise ValueError(f"not a geometry attribute: {key}")
            segment.directive.set(key, value)
        if "w" in geometry or "h" in geometry:
            # A card somebody sized by hand is no longer the content's to size.
            # This used to be implicit -- carrying `w`/`h` at all meant "not
            # auto" -- and that signal disappeared the day every card started
            # carrying them (see `measured`). Said out loud now, because the
            # alternative is a card that silently re-measures itself back over
            # the size the user just chose.
            segment.directive.set("auto", "false")
        self.dirty = True
        return True

    def arrange(self, placements: list[dict[str, Any]]) -> bool:
        """Apply a whole gesture at once, or none of it.

        One drag is one arrangement: the card under the finger *and* everyone
        who stepped aside for it. Applied one at a time, the layout passes
        through states that are genuinely overlapping -- grow a card before its
        neighbour has moved and the two share cells for an instant -- so a
        per-placement check would refuse the resize and let the neighbour move
        anyway. That is exactly the "they avoided, then came back on top of each
        other" this is meant to make impossible.

        So the *result* is what is checked, not the steps. All or nothing, and
        the check is on the arrangement as a whole.
        """
        from . import analysis

        wanted = {str(spot["id"]): spot for spot in placements}
        if any(self.find(card_id) is None for card_id in wanted):
            return False

        after = []
        for card in self.cards:
            spot = wanted.get(card["id"])
            if spot is None:
                after.append(card)
                continue
            after.append({
                **card,
                **{key: int(spot[key]) for key in ("x", "y", "w", "h", "page")
                   if key in spot and spot[key] is not None},
            })
        if analysis.overlapping(after):
            return False

        for card_id, spot in wanted.items():
            self.place(
                card_id,
                _checked=False,
                **{key: int(spot[key]) for key in ("x", "y", "w", "h")
                   if key in spot and spot[key] is not None},
            )
        return True

    def _room_for(self, card_id: str, geometry: dict[str, Any]) -> bool:
        """Whether `card_id` would still be alone on its cells after this."""
        from . import analysis

        cards = self.cards
        card = next((entry for entry in cards if entry.get("id") == card_id), None)
        if card is None:
            return True

        after = {**card, **{k: v for k, v in geometry.items() if v is not None}}
        if after.get("w") is None or after.get("h") is None:
            # Sized by its content: the document cannot know what it covers, so
            # it cannot be asked to prove it covers nothing.
            return True
        box = (int(after.get("x", 0)), int(after.get("y", 0)),
               int(after["w"]), int(after["h"]))
        return analysis.fits(cards, card_id, box, int(after.get("page", 0) or 0))

    def measured(self, card_id: str, w: int, h: int) -> bool:
        """Record what a self-sizing card measured itself at.

        Written like any other size, with one difference: `auto` is left set,
        because it says the size came from the content rather than from a
        person, and the card goes on re-measuring. What it buys is that the
        document knows what *every* card covers -- see `place`, which cannot
        refuse an overlap with a card whose footprint is unknown.
        """
        segment = self._writable(card_id)
        if segment is None:
            return False
        if not self._room_for(card_id, {"w": w, "h": h}):
            return False
        if (segment.directive.attrs.get("w") == str(w)
                and segment.directive.attrs.get("h") == str(h)):
            return True
        segment.directive.set("w", w)
        segment.directive.set("h", h)
        segment.directive.set("auto", "true")
        self.dirty = True
        return True

    def untangle(self) -> list[str]:
        """Move cards apart until nothing shares a cell. Returns what moved.

        The board's model is that placement is *refused* rather than resolved,
        and the algorithm that makes room for a drag begins by assuming nothing
        overlaps. A document that arrives overlapping -- hand-edited, merged, or
        written by a build with the bug this exists because of -- makes that
        assumption false, and from then on every gesture behaves inexplicably.

        So this is a repair, not a layout engine: cards are visited in file
        order, the first to claim a cell keeps it, and anything on top of it is
        swept to the first free row below. Nobody's arrangement is optimised and
        nothing moves that did not have to.
        """
        from . import analysis

        cards = self.cards
        if not analysis.overlapping(cards):
            return []

        moved: list[str] = []
        settled: list[dict[str, Any]] = []
        for card in cards:
            if card.get("w") is None or card.get("h") is None:
                # Unsized cards take room the document cannot know about, so
                # they are left exactly where they are rather than moved on a
                # guess.
                settled.append(card)
                continue
            page = int(card.get("page", 0) or 0)
            box = (int(card.get("x", 0)), int(card.get("y", 0)),
                   int(card["w"]), int(card["h"]))
            if analysis.fits(settled, card["id"], box, page):
                settled.append(card)
                continue
            y = box[1]
            while not analysis.fits(settled, card["id"], (box[0], y, box[2], box[3]), page):
                y += 1
            self.place(card["id"], y=y)
            moved.append(card["id"])
            settled.append({**card, "y": y})
        return moved

    def next_id(self, prefix: str = "c") -> str:
        """A reference nobody has to think about.

        Ids are generated and never shown, which is what lets a title be prose:
        two cards may both be called "threshold" without either of them losing
        the thing another card names it by.
        """
        taken = {card["id"] for card in self.cards}
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        return f"{prefix}{n}"

    def add_card(self, card_id: str, kind: str = "code", **attrs: Any) -> bool:
        """Append a card. Returns False if the id is taken."""
        if self.find(card_id) is not None:
            return False
        if not self.annotated:
            self._annotate()
        values = {"id": card_id, "kind": kind}
        values.update({key: str(value) for key, value in attrs.items() if value is not None})
        self.segments.append(Segment(Directive(kind="card", attrs=values, raw="", rewritten=True), ""))
        self.dirty = True
        return True

    def add_output(self, operation: str, label: str, expression: str,
                   card_id: str | None = None) -> str | None:
        """Write a `print` or a `save` into the program, as its own card.

        This is what "save this" means, and why it is a document change rather
        than a button that writes a file: a `save` is an *effect the program
        declares*. Put in the text, it can be read, diffed, committed, and run
        again tomorrow by somebody with no UI at all -- which is the difference
        between a workspace and a scratchpad.

        Returns the new card's id.
        """
        if operation not in ("print", "save"):
            raise ValueError("an output is a print or a save")
        if not expression:
            return None

        identity = card_id or self.next_id(operation)
        if not self.annotated:
            self._annotate()
        body = f'{operation} "{_escape(label)}" {expression}\n'
        self.segments.append(
            Segment(
                Directive(
                    kind="card",
                    attrs={"id": identity, "title": label, "kind": operation,
                           "node": expression},
                    raw="",
                    rewritten=True,
                ),
                body,
            )
        )
        self.dirty = True
        return identity

    def tidy(self) -> bool:
        """Put the cards in an order where every name is defined before use.

        Returns whether anything moved. The order is derived from the language
        itself (see analysis.py), so what this produces is always a program the
        engine would accept -- and a card can be dragged anywhere on the board
        without anybody having to think about where its text ends up.

        Only whole cards move, and only relative to each other: the preamble,
        the board directive and every card's own text stay exactly as they are.
        """
        from . import analysis

        cards = self.cards
        if len(cards) < 2:
            return False
        wanted = analysis.dependency_order(cards)
        current = [card["id"] for card in cards]
        if wanted == current:
            return False

        by_id = {}
        head: list[Segment] = []
        for segment in self.segments:
            directive = segment.directive
            if directive is not None and directive.kind == "card":
                by_id[directive.attrs.get("id")] = segment
            else:
                head.append(segment)
        self.segments = head + [by_id[card_id] for card_id in wanted if card_id in by_id]
        self.dirty = True
        return True

    # ------------------------------------------------------ cut and paste

    def fragment(self, card_ids: list[str]) -> str:
        """Those cards, as .imgql text.

        The cut buffer is the file format. Not a private JSON payload beside it:
        a fragment of a workspace *is* a fragment of a program, so what lands on
        the system clipboard can be pasted into a text editor and read, mailed
        to somebody, or pasted back here -- and there is no second format to keep
        in step with this one.
        """
        wanted = [card_id for card_id in card_ids]

        if not self.annotated:
            # A file nobody has arranged has no card directives to copy, but it
            # does have cards -- derived, and one of them holds the whole
            # program (see `_unarranged`). Copying used to return nothing here,
            # so cut and copy quietly did nothing on the commonest document
            # there is: one somebody has just opened.
            out = []
            for card in self.cards:
                if card["id"] not in wanted:
                    continue
                attrs = {"id": card["id"], "title": card.get("title", card["id"]),
                         "kind": card.get("kind", "code")}
                if card.get("node"):
                    attrs["node"] = card["node"]
                pairs = " ".join(f"{k}={_quote(k, str(v))}" for k, v in attrs.items())
                body = card.get("source") or ""
                out.append(f"//@card {pairs}\n")
                if body:
                    out.append(body if body.endswith("\n") else body + "\n")
            return "".join(out)

        out: list[str] = []
        for segment in self.segments:
            directive = segment.directive
            if directive is None or directive.kind != "card":
                continue
            if directive.attrs.get("id") not in wanted:
                continue
            out.append(directive.render())
            if segment.body:
                out.append(segment.body if segment.body.endswith("\n") else segment.body + "\n")
        return "".join(line if line.endswith("\n") else line + "\n" for line in out)

    def import_fragment(self, text: str, **overrides: Any) -> list[str]:
        """Add the cards in `text` to this document. Returns their new ids.

        Everything that could collide is renamed rather than refused. Ids are
        minted fresh, because an id is this document's way of naming the card
        and the incoming one means nothing here. Bindings -- the `let` names the
        pasted code defines -- are renamed only when this document already
        defines them, and the references *within the pasted cards* are rewritten
        to match, so a pasted group still computes what it computed where it
        came from.
        """
        incoming = parse(text)
        if not incoming.annotated:
            # Plain text pasted from anywhere: one code card holding it.
            incoming = parse(f'//@card id=x kind=code\n{text if text.endswith(chr(10)) else text + chr(10)}')

        taken = {name for card in self.cards for name in bindings_in(card.get("source", ""))}
        renames: dict[str, str] = {}
        for card in incoming.cards:
            for name in bindings_in(card.get("source", "")):
                if name in taken and name not in renames:
                    renames[name] = _free_name(name, taken | set(renames.values()))
                taken.add(renames.get(name, name))

        made: list[str] = []
        for card in incoming.cards:
            new_id = self.next_id()
            attrs = {
                key: value
                for key, value in card.items()
                if key in ("kind", "title", "x", "y", "w", "h", "page", "node", "view", "aspect")
                and value is not None
            }
            attrs.update({key: value for key, value in overrides.items() if value is not None})
            if "node" in attrs and attrs["node"] in renames:
                attrs["node"] = renames[attrs["node"]]
            self.add_card(new_id, str(attrs.pop("kind", "code")), **attrs)
            source = card.get("source", "")
            for before, after in renames.items():
                source = re.sub(rf"\b{re.escape(before)}\b", after, source)
            if source:
                self.set_source(new_id, source)
            made.append(new_id)
        return made

    def duplicate_card(self, card_id: str, new_id: str, **attrs: Any) -> bool:
        """Copy a card, body and all, as a new card.

        A copy that lost the text would not be a copy of anything a user
        recognises: what they pointed at was the card *with what is in it*.
        Everything else about it comes along too, so a copy of a constrained,
        node-bound result card is another one of those.
        """
        source = self.find(card_id)
        if source is None or source.directive is None or self.find(new_id) is not None:
            return False
        values = dict(source.directive.attrs)
        values["id"] = new_id
        values.update({key: str(value) for key, value in attrs.items() if value is not None})
        self.segments.append(
            Segment(Directive(kind="card", attrs=values, raw="", rewritten=True), source.body)
        )
        self.dirty = True
        return True

    def remove_card(self, card_id: str) -> bool:
        segment = self.find(card_id)
        if segment is None:
            return False
        self.segments.remove(segment)
        self.dirty = True
        return True

    def set_attr(self, card_id: str, key: str, value: Any) -> bool:
        segment = self._writable(card_id)
        if segment is None:
            return False
        segment.directive.set(key, value)
        self.dirty = True
        return True

    def set_source(self, card_id: str, text: str) -> bool:
        """Replace a card's body. Only that card's text moves."""
        segment = self._writable(card_id)
        if segment is None:
            return False
        kind = segment.directive.attrs.get("kind", "code") if segment.directive else "code"
        segment.body = _comment(text) if kind == "note" else text
        self.dirty = True
        return True


def parse(text: str) -> Document:
    """Split a file into segments without changing a byte of it."""
    lines = text.splitlines(keepends=True)
    segments: list[Segment] = []
    preamble: list[str] = []
    current: Segment | None = None
    annotated = False

    for line in lines:
        match = _DIRECTIVE.match(line.rstrip("\r\n"))
        if match:
            annotated = True
            attrs: dict[str, str] = {}
            for key, quoted, bare in _ATTR.findall(match.group(2)):
                attrs[key] = bare if bare else _unescape(quoted)
            directive = Directive(match.group(1), attrs, line.rstrip("\r\n"))
            current = Segment(directive, "")
            segments.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current.body += line

    if preamble or not segments:
        segments.insert(0, Segment(None, "".join(preamble)))
    return Document(segments=segments, source=text, annotated=annotated)
