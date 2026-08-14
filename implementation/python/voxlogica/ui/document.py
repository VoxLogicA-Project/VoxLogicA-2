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
_ATTR = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=(?:"([^"]*)"|(\S+))')

#: Written in this order when a directive is regenerated, so that moving one card
#: produces a one-line diff instead of a reshuffled file. Keys not listed keep
#: their original relative order and come last -- an attribute this build does
#: not understand still survives a round trip.
_KEY_ORDER = (
    "id",
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
    "title",
    "cols",
    "rows",
)

_GEOMETRY = ("x", "y", "w", "h", "page")


def _quote(value: str) -> str:
    """Quote only when the value could not be read back as one token."""
    return f'"{value}"' if value == "" or re.search(r"[\s\"]", value) else value


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
        pairs = " ".join(f"{key}={_quote(self.attrs[key])}" for key in known + rest)
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
                    "title": "program",
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
            card: dict[str, Any] = {
                "id": attrs.get("id") or f"card{index}",
                "kind": attrs.get("kind", "code"),
                "x": directive.int_or("x", 0),
                "y": directive.int_or("y", 0),
                "page": directive.int_or("page", 0),
            }
            for key in ("w", "h", "minW", "minH", "maxW", "maxH"):
                value = directive.int_or(key, None)
                if value is not None:
                    card[key] = value
            for key in ("title", "node", "view"):
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
            # card is: a note's text lives there exactly as a program's does, and
            # only exposing it for code cards would make a note's own content
            # invisible to the UI that has to render it.
            card["source"] = segment.body
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
            {"id": "program", "kind": "code", "x": "0", "y": "0"},
            "",
            rewritten=True,
        )
        self.segments = [Segment(board, ""), Segment(card, self.source)]
        self.annotated = True

    def place(self, card_id: str, **geometry: int) -> bool:
        """Move or resize a card. Returns False if there is no such card."""
        segment = self.find(card_id)
        if segment is None or segment.directive is None:
            if not self.annotated and card_id == "program":
                self._annotate()
                self.dirty = True
                segment = self.find(card_id)
            if segment is None or segment.directive is None:
                return False
        for key, value in geometry.items():
            if key not in _GEOMETRY and key not in ("w", "h"):
                raise ValueError(f"not a geometry attribute: {key}")
            segment.directive.set(key, value)
        # A card the user has sized is no longer the content's to size, and the
        # file says so by carrying w/h at all.
        self.dirty = True
        return True

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

    def remove_card(self, card_id: str) -> bool:
        segment = self.find(card_id)
        if segment is None:
            return False
        self.segments.remove(segment)
        self.dirty = True
        return True

    def set_attr(self, card_id: str, key: str, value: Any) -> bool:
        segment = self.find(card_id)
        if segment is None or segment.directive is None:
            return False
        segment.directive.set(key, value)
        self.dirty = True
        return True

    def set_source(self, card_id: str, text: str) -> bool:
        """Replace a code card's body. Only that card's text moves."""
        segment = self.find(card_id)
        if segment is None:
            if not self.annotated and card_id == "program":
                self._annotate()
                segment = self.find(card_id)
            if segment is None:
                return False
        segment.body = text
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
                attrs[key] = quoted if bare == "" else bare
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
