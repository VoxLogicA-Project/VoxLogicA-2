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
    "view",
    "from",
    "cols",
    "rows",
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
_ALWAYS_QUOTED = ("title",)


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
            return [
                {
                    "id": "program",
                    "kind": "code",
                    "title": "Program",
                    "x": 0,
                    "y": 0,
                    "page": 0,
                    "auto": True,
                    "source": self.source,
                }
            ]

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
            for key in ("node", "view", "from"):
                if key in attrs:
                    card[key] = attrs[key]
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
                parts.append(segment.directive.render())
                parts.append("\n")
            parts.append(segment.body)
        return "".join(parts)

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

    def place(self, card_id: str, **geometry: int) -> bool:
        """Move or resize a card. Returns False if there is no such card."""
        segment = self._writable(card_id)
        if segment is None:
            return False
        for key, value in geometry.items():
            if key not in _GEOMETRY and key not in ("w", "h"):
                raise ValueError(f"not a geometry attribute: {key}")
            segment.directive.set(key, value)
        # A card the user has sized is no longer the content's to size, and the
        # file says so by carrying w/h at all.
        self.dirty = True
        return True

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
