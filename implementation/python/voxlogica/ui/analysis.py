"""What a card defines, and what it needs -- asked of the real parser.

The board lets people arrange cards in space, and space has nothing to do with
the order a program is read in. Something has to know that a card saying
`let mask = threshold(flair, 0.6)` cannot be written before the one that defines
`flair`, and that something must be *the language itself*: a regular expression
that guessed at it would be a second, worse understanding of .imgql living beside
the real one, and the two would disagree on the day somebody wrote a `for` or a
nested `let`.

So this module imports `voxlogica.parser` -- the same front end the engine uses --
and walks its AST. Defined names come from `Declaration`; used names are every
identifier an expression mentions (`ECall`, which is what a bare name parses to)
minus the ones bound inside it: a declaration's own parameters, a local `let`,
and the loop variable of `for` and `filter`.

Nothing here decides whether a name is a primitive or a user definition. It does
not need to: a card only depends on another card if that other card *defines* the
name, and `threshold` being a primitive simply means no card defines it.

A card that does not parse -- half-typed, mid-edit, or in a dialect this build
does not know -- reports `None` rather than a guess, and the caller leaves it
exactly where it is. Reordering source you did not understand is how an editor
loses somebody's work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Uses:
    """The names a fragment defines, and the free names it mentions."""

    defines: frozenset[str] = field(default_factory=frozenset)
    needs: frozenset[str] = field(default_factory=frozenset)


def _walk(node: Any, bound: frozenset[str], out: set[str]) -> None:
    """Collect free identifiers, honouring whatever binds inside the node."""
    from voxlogica import parser as vp

    if isinstance(node, vp.ECall):
        # A bare name parses as a call with no arguments, so this is both "uses
        # x" and "calls f(x)"; either way the identifier is a reference unless
        # something in scope bound it.
        if node.identifier not in bound:
            out.add(node.identifier)
        for argument in node.arguments:
            _walk(argument, bound, out)
    elif isinstance(node, vp.ELet):
        _walk(node.value, bound, out)
        _walk(node.body, bound | {node.variable}, out)
    elif isinstance(node, (vp.EFor, vp.EFilter)):
        _walk(node.iterable, bound, out)
        body = getattr(node, "body", None) or getattr(node, "predicate", None)
        if body is not None:
            _walk(body, bound | {node.variable}, out)
    elif isinstance(node, vp.EFold):
        if node.init is not None:
            _walk(node.init, bound, out)
        _walk(node.sequence, bound, out)
    elif isinstance(node, vp.EArray):
        for item in node.items:
            _walk(item, bound, out)
    elif isinstance(node, vp.ESlice):
        for value in vars(node).values():
            if isinstance(value, vp.Expression):
                _walk(value, bound, out)
    # Numbers, booleans and strings mention nobody.


def analyse(source: str) -> Uses | None:
    """What this fragment defines and needs, or `None` if it does not parse."""
    from voxlogica import parser as vp

    text = (source or "").strip()
    if not text:
        return Uses()
    try:
        program = vp.parse_program_content(source)
    except Exception as exc:  # noqa: BLE001 - any parse failure means "unknown"
        logger.debug("card did not parse, so it is left where it is (%s)", exc)
        return None

    defines: set[str] = set()
    needs: set[str] = set()
    for command in program.commands:
        if isinstance(command, vp.Declaration):
            # Read before the name is added: a declaration that mentions its own
            # name is recursion, and the point of finding it is to refuse it.
            _walk(command.expression, frozenset(command.arguments), needs)
            defines.add(command.identifier)
        else:
            expression = getattr(command, "expression", None)
            if expression is not None:
                _walk(expression, frozenset(), needs)
    return Uses(frozenset(defines), frozenset(needs) - defines)


@dataclass(frozen=True)
class Output:
    """A `print` or a `save`: an output the program itself declared."""

    #: "print" or "save". They are kept apart because they are not the same
    #: thing wearing two names: a print is a value shown, a save is an effect
    #: with a destination, and a card for each says so.
    operation: str
    #: The quoted label, which is what the author called this output.
    label: str
    #: The expression, as written.
    expression: str
    #: The expression when it is a bare name, so it can be bound to a node
    #: without anyone hashing a sub-expression. `None` for anything else --
    #: honest until a selection can be resolved to a hash on its own.
    binding: str | None


def outputs(source: str) -> list[Output]:
    """The outputs a program declares, in file order.

    These are what a document *says* it produces, so they are what a board
    should show when nobody has arranged one yet. Asked of the real parser, for
    the same reason everything else in this module is: a regular expression
    looking for `print` would find one inside a string, inside a comment, and
    inside the word `sprint`.
    """
    from voxlogica import parser as vp

    if not (source or "").strip():
        return []
    try:
        program = vp.parse_program_content(source)
    except Exception as exc:  # noqa: BLE001 - a file mid-edit declares nothing
        logger.debug("could not read the outputs of a document (%s)", exc)
        return []

    found: list[Output] = []
    for command in program.commands:
        if isinstance(command, vp.Print):
            operation = "print"
        elif isinstance(command, vp.Save):
            operation = "save"
        else:
            continue
        expression = command.expression
        # `print "s" s` is the common shape and the one worth binding: a bare
        # name is already a node the reducer can be asked about. Anything
        # larger needs a hash for a sub-expression, which is a separate piece
        # of machinery, so it is left unbound rather than guessed at.
        binding = (
            expression.identifier
            if isinstance(expression, vp.ECall) and not expression.arguments
            else None
        )
        found.append(
            Output(
                operation=operation,
                label=command.identifier,
                expression=expression.to_syntax(),
                binding=binding,
            )
        )
    return found


class Cycle(ValueError):
    """Cards that need each other. There is no order that satisfies them."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        super().__init__("these cards need each other: " + " → ".join(ids))


def dependency_order(cards: list[dict[str, Any]]) -> list[str]:
    """The ids of `cards`, in an order where every name is defined before use.

    Stable: cards that do not depend on each other keep the order they already
    had, so writing the file back produces the smallest diff that is still a
    correct program rather than a reshuffle.

    Cards whose source does not parse are pinned where they are -- they cannot
    be reasoned about, and moving them would be reordering text nobody
    understood.
    """
    facts = {card["id"]: analyse(card.get("source", "")) for card in cards}
    provider: dict[str, str] = {}
    for card in cards:
        seen = facts[card["id"]]
        if seen is None:
            continue
        for name in seen.defines:
            # First definition wins; a duplicate is a separate complaint, and
            # ordering should still produce something usable.
            provider.setdefault(name, card["id"])

    order = [card["id"] for card in cards]
    position = {card_id: index for index, card_id in enumerate(order)}
    pinned = {card["id"] for card in cards if facts[card["id"]] is None}

    edges: dict[str, set[str]] = {card_id: set() for card_id in order}
    for card in cards:
        seen = facts[card["id"]]
        if seen is None:
            continue
        for name in seen.needs:
            source = provider.get(name)
            if source is not None and source != card["id"]:
                edges[card["id"]].add(source)

    done: list[str] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(card_id: str) -> None:
        if state.get(card_id) == 2:
            return
        if state.get(card_id) == 1:
            raise Cycle([*stack[stack.index(card_id) :], card_id])
        state[card_id] = 1
        stack.append(card_id)
        for needed in sorted(edges[card_id], key=lambda other: position[other]):
            visit(needed)
        stack.pop()
        state[card_id] = 2
        done.append(card_id)

    for card_id in order:
        visit(card_id)

    if pinned:
        # A card nobody could read keeps its index; the rest close ranks around
        # it in dependency order.
        movable = [card_id for card_id in done if card_id not in pinned]
        result: list[str] = []
        moving = iter(movable)
        for card_id in order:
            result.append(card_id if card_id in pinned else next(moving))
        return result
    return done


def duplicates(cards: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Names defined by more than one card, and who defines them.

    Not an error to be raised: it is a fact about the document that the UI can
    show while somebody is still typing. A file with two `let mask` is
    ambiguous, and the first thing anybody wants is to be told which two cards.
    """
    where: dict[str, list[str]] = {}
    for card in cards:
        seen = analyse(card.get("source", ""))
        if seen is None:
            continue
        for name in sorted(seen.defines):
            where.setdefault(name, []).append(card["id"])
    return {name: ids for name, ids in where.items() if len(ids) > 1}


# ------------------------------------------------------------------- geometry


def _box(card: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """A card's cells, or `None` for one the board has not sized yet.

    A card with no `w`/`h` is sized by its content at render time, so the
    document does not know how much room it takes. Guessing small is what let
    other cards be grown over it, so it is left out of the question entirely --
    unknown is not the same as tiny.
    """
    if card.get("w") is None or card.get("h") is None:
        return None
    return (
        int(card.get("x", 0)),
        int(card.get("y", 0)),
        int(card["w"]),
        int(card["h"]),
    )


def overlapping(cards: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Pairs of cards that share a cell, on the same page.

    The board's whole model rests on this being empty: placement is refused
    rather than resolved, and the algorithm that makes room for a drag starts
    from the premise that nothing overlaps yet. Once two cards share a cell that
    premise is false, and every gesture afterwards behaves inexplicably -- which
    is what makes this worth being able to *ask*.
    """
    boxes = []
    for card in cards:
        box = _box(card)
        if box is not None:
            boxes.append((card.get("id", ""), int(card.get("page", 0) or 0), box))

    found: list[tuple[str, str]] = []
    for index, (first, page, (ax, ay, aw, ah)) in enumerate(boxes):
        for second, other_page, (bx, by, bw, bh) in boxes[index + 1:]:
            if page != other_page:
                continue
            if ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay:
                continue
            found.append((first, second))
    return found


def fits(cards: list[dict[str, Any]], card_id: str, box: tuple[int, int, int, int],
         page: int = 0) -> bool:
    """Whether `card_id` may occupy `box` without landing on anybody."""
    x, y, w, h = box
    for card in cards:
        if card.get("id") == card_id:
            continue
        if int(card.get("page", 0) or 0) != page:
            continue
        other = _box(card)
        if other is None:
            continue
        ox, oy, ow, oh = other
        if x + w <= ox or ox + ow <= x or y + h <= oy or oy + oh <= y:
            continue
        return False
    return True
