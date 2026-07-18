"""Schedule-time kernel fusion: cones over the ready frontier ("semantic queueing").

See ``doc/specs/semantic-queueing-fusion.md`` for the full design and its
justification. Summary of the mechanism this module implements (§3):

Fusion runs exactly once per dispatch unit, at pop time — the moment a worker
takes a node off the ready queue, which is the last responsible moment before
execution (§3.0). From that seed, ``FusionPlanner.plan`` grows a "cone": a
maximal set of elementwise nodes reachable by walking *forward* into
consumers that are RIPE — every one of their other dependencies is either
already completed, or is itself another member of the cone being built. This
is the crucial subtlety: a cone member has been *claimed* (``NodeTable.begin``)
but has not yet been *completed* (``DependencyGraph.on_complete``/``_finish``),
so the live scheduler still sees it as unmet — its consumers' ``pending``
counts have not yet been decremented, so those consumers are NOT sitting in
the ready queue. Growing into them is therefore never growing into
queue-resident work; it only pre-empts scheduling steps that were already
guaranteed to happen, in order (§3.0's "ripeness" rule — this is what makes
fusion a pure win with no fuse/split churn: nothing here is ever undone).

A cone never crosses a SimpleITK op boundary: only primitives whose
``PrimitiveSpec.elementwise`` is set are absorbable (§2). Real sitk work
(morphology, distance transforms, resampling, ...) is where a cone always
stops, and the normal single-node dispatch path runs it unchanged.

Growth is O(cone size × node degree) — only dict/set lookups and
``DependencyGraph._dependents`` list walks, never a traversal of the whole
graph or plan, preserving the engine's central O(frontier) discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from voxlogica.lazy.ir import NodeId

if TYPE_CHECKING:
    from voxlogica.engine.graph import DependencyGraph
    from voxlogica.engine.node_table import NodeTable
    from voxlogica.primitives.registry import PrimitiveRegistry


@dataclass(frozen=True)
class Cone:
    """A planned fusion unit: elementwise members, in topological order.

    ``members_topo`` is already topologically sorted for free — a member is
    only appended to the cone once every in-cone dependency it has is already
    present, which is exactly the definition of a topological order for a DAG
    built incrementally this way. No separate sort is needed.
    """

    members_topo: tuple[NodeId, ...]
    inputs: frozenset[NodeId]     # external (already-completed) dependency ids
    exits: frozenset[NodeId]      # members with a consumer outside the cone, or a goal
    interiors: frozenset[NodeId]  # members consumed only within the cone

    def __len__(self) -> int:
        return len(self.members_topo)


def _elementwise_spec(registry: "PrimitiveRegistry", node: Any) -> Any | None:
    """A node's ``ElementwiseSpec``, or None if it is not fusable."""
    if node.kind != "primitive":
        return None
    try:
        spec = registry.get_spec(node.operator)
    except KeyError:
        return None
    return spec.elementwise


def _materialized_shape(table: "NodeTable", dep_id: NodeId) -> tuple[int, ...] | None:
    """The resident shape of a dependency's value, or None (scalar/no shape)."""
    value = table.values.get(dep_id)
    shape = getattr(value, "shape", None)
    return tuple(shape) if shape is not None else None


class FusionPlanner:
    """Grows and claims elementwise cones from a ready-popped seed node."""

    def __init__(self, registry: "PrimitiveRegistry"):
        self.registry = registry

    def plan(self, seed: NodeId, *, graph: "DependencyGraph", table: "NodeTable",
             goals: set[NodeId], cap: int) -> Cone | None:
        """Plan a cone seeded at ``seed``, claiming every member; None if not
        worth fusing (not elementwise, or growth found no partner).

        All of ``seed``'s dependencies must already be resident in
        ``table.values`` (the caller is responsible for rematerializing them
        first, exactly as the single-node dispatch path already does for its
        one node — see ``ComputationEngine._worker``).
        """
        seed_node = table.nodes[seed]
        if _elementwise_spec(self.registry, seed_node) is None:
            return None

        ref_shape = None
        for dep in graph.deps(seed):
            shape = _materialized_shape(table, dep)
            if shape is not None:
                ref_shape = shape
                break

        cone_members: list[NodeId] = [seed]
        cone_set: set[NodeId] = {seed}
        frontier: list[NodeId] = [seed]

        while frontier and len(cone_set) < cap:
            current = frontier.pop()
            for consumer in graph._dependents.get(current, ()):
                if consumer in cone_set or len(cone_set) >= cap:
                    continue
                node = table.nodes.get(consumer)
                if node is None or _elementwise_spec(self.registry, node) is None:
                    continue
                if not table.is_claimable(consumer):
                    continue  # already running/materialized elsewhere — skip, don't fail the cone
                deps = graph.deps(consumer)
                if not all(d in cone_set or d in table.completed for d in deps):
                    continue  # not ripe: some input is neither completed nor an in-cone member
                if ref_shape is not None and any(
                    _materialized_shape(table, d) not in (None, ref_shape)
                    for d in deps if d not in cone_set
                ):
                    continue  # shape mismatch: cut the cone here (§3.1)
                cone_set.add(consumer)
                cone_members.append(consumer)
                frontier.append(consumer)

        if len(cone_set) < 2:
            return None  # no partner found — not worth a cone, take the normal path

        # Claim every member. is_claimable() was already checked for every
        # non-seed member during growth and nothing else can have claimed them
        # since (planning is synchronous, single-writer, on the event loop —
        # see graph.py's module invariant) — begin() cannot raise here.
        for member in cone_members:
            table.begin(member)

        exits: set[NodeId] = set()
        interiors: set[NodeId] = set()
        for member in cone_members:
            consumers = graph._dependents.get(member, ())
            if member in goals or any(c not in cone_set for c in consumers):
                exits.add(member)
            else:
                interiors.add(member)

        inputs = frozenset(
            dep
            for member in cone_members
            for dep in graph.deps(member)
            if dep not in cone_set
        )

        return Cone(
            members_topo=tuple(cone_members),
            inputs=inputs,
            exits=frozenset(exits),
            interiors=frozenset(interiors),
        )
