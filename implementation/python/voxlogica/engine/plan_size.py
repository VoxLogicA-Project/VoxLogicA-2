"""How many nodes this run will end up with.

The progress bar can only forecast if it knows the size of the finished plan,
and on a plan that unrolls at runtime that number is not available from the
counters. `registered_total` counts what has been unrolled so far; on a BraTS
sweep it grew from 42k to 12M while `registered_total - completed` sat near
36,500 for hours, so an ETA computed from it read two minutes for seven hours.

Nor can the size be read off the program text alone: a `for` node holds its body
as an unreduced AST inside a closure, so the body's node count does not exist
until one body has been reduced.

What does work is an abstraction of the plan measured on the run itself:

    N  =  base  +  units_total x cost_per_unit

  units      the bodies of the INNERMOST loop level. Their number is the product
             of the loop cardinalities along the nesting chain, so a 12-body
             loop inside a 90-body loop means 1080 units -- known as soon as one
             inner loop opens, rather than after all 90 have.
  base       nodes registered before the innermost level began: the setup a
             sweep shares across every unit, counted once and not per unit.
  cost_per_unit  the marginal nodes each further unit has cost so far.

This is deliberately *not* a count of syntactic nodes times a multiplicity: the
plan is hash-consed, so anything a body does not derive from its loop variable
is one shared node, however many iterations there are. Measuring the marginal
cost of units already reduced captures that sharing without having to reason
about which subexpressions depend on the variable.

The estimate is static in form -- structural, from cardinalities the plan
declares -- and dynamic in time: cardinalities and nesting depth are learned as
loops open, so it is recomputed rather than fixed at startup. It rises when a
deeper level of nesting appears, which is the honest direction: a plan can only
turn out bigger than the nesting seen so far implies, never smaller.
"""

from __future__ import annotations

from dataclasses import dataclass

from voxlogica.lazy.ir import NodeId


@dataclass
class _Loop:
    cardinality: int
    chain: int          # bodies of this loop in the whole plan, nesting included
    reduced: int = 0    # bodies whose nodes are already registered


class PlanSizeEstimator:
    """Running estimate of the finished plan's node count."""

    def __init__(self) -> None:
        self._loops: dict[NodeId, _Loop] = {}
        self._base: int | None = None       # registered nodes before the deepest level
        self._deepest = 0                   # largest chain seen

    def open_loop(self, loop_id: NodeId, parent_id: NodeId | None,
                  cardinality: int, registered_now: int) -> None:
        """Record a loop as it starts unrolling."""
        if cardinality <= 0:
            return
        parent = self._loops.get(parent_id) if parent_id is not None else None
        chain = cardinality * (parent.chain if parent is not None else 1)
        self._loops[loop_id] = _Loop(cardinality=cardinality, chain=chain)
        if chain > self._deepest:
            # A deeper level than any seen: everything registered so far is
            # shared setup with respect to it, so the baseline moves here.
            self._deepest = chain
            self._base = registered_now

    def note_reduced(self, loop_id: NodeId, reduced: int) -> None:
        """Record how many of a loop's bodies have been reduced so far."""
        loop = self._loops.get(loop_id)
        if loop is not None:
            loop.reduced = reduced

    def close_loop(self, loop_id: NodeId) -> None:
        """A loop finished unrolling; its bodies stay counted."""
        loop = self._loops.get(loop_id)
        if loop is not None:
            loop.reduced = loop.cardinality

    def estimate(self, registered_now: int) -> int | None:
        """Projected node count of the finished plan, or None if unknown yet."""
        if self._base is None or self._deepest <= 0:
            return None
        units_reduced = sum(loop.reduced for loop in self._loops.values()
                            if loop.chain == self._deepest)
        if units_reduced <= 0:
            return None
        marginal = registered_now - self._base
        if marginal <= 0:
            return None
        cost_per_unit = marginal / units_reduced
        # Never below what is already registered: the estimate is a forecast of
        # the total, and the total cannot be smaller than the part.
        return max(registered_now, int(self._base + self._deepest * cost_per_unit))

    def describe(self, registered_now: int) -> dict[str, int]:
        """Internals, for the memory log and for tests."""
        units_reduced = sum(loop.reduced for loop in self._loops.values()
                            if loop.chain == self._deepest)
        return {
            "loops": len(self._loops),
            "units_total": self._deepest,
            "units_reduced": units_reduced,
            "base": self._base or 0,
            "estimate": self.estimate(registered_now) or 0,
        }
