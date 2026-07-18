"""Dataflow firing and value lifetime: the dependency graph's mutable core.

This is Kahn-style dataflow scheduling made incremental: a node is *registered*
with a count of unmet dependencies, fires (becomes ready) when the count hits
zero, and on completion decrements its dependents' counts — every transition is
O(node degree). Value lifetime is reference counting: each registered consumer
holds one reference to each of its inputs; the last release evicts the value
from the live tier.

INVARIANTS
- Per-node scheduling state (pending count, dependents list, priority, deps
  memo) exists only between ``register`` and ``on_complete``. Completed nodes
  leave behind exactly two monotone facts: membership in ``table.completed``
  and their (shared, hash-consed) spec in ``table.nodes``. This is what keeps
  the scheduler's working set proportional to the *frontier*, not the plan.
- ``incomplete`` is the frontier: nodes registered and not yet completed.
  A dependency is *unmet* iff it is in ``incomplete``; anything else is either
  already completed or was pruned at registration because the disk tier holds
  it (loadable on demand) — both count as available. There is exactly one such
  rule, used by every registration path (the old engine had a second,
  hand-rolled copy in loop expansion that disagreed on persisted-but-pruned
  bodies and could deadlock a partially-warm cache).
- ``consumers`` may outlive completion (a completed value stays pinned until
  its last consumer runs) but is dropped the moment it reaches zero.

All mutation happens on the event loop (single writer, no locks). The liveness
probe reads ``incomplete``/``consumers`` membership from the persister thread;
single dict/set lookups are atomic under the GIL and staleness is harmless
(see liveness.py).
"""

from __future__ import annotations

from collections import defaultdict

from voxlogica.engine.expander import Expander
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeId


class DependencyGraph:
    """Pending-count firing + consumer refcounts over the node table."""

    def __init__(self, table: NodeTable):
        self.table = table
        self.incomplete: set[NodeId] = set()          # the frontier
        self.pending: dict[NodeId, int] = {}          # unmet deps before ready
        self.consumers: dict[NodeId, int] = {}        # unrun consumers holding a value
        self.protected: set[NodeId] = set()           # goal values: never auto-evicted
        self._dependents: dict[NodeId, list[NodeId]] = defaultdict(list)
        self._deps_memo: dict[NodeId, frozenset[NodeId]] = {}
        self.registered_total = 0                     # monotone, for progress totals

    # ── Structure ─────────────────────────────────────────────────────────────

    def deps(self, nid: NodeId) -> frozenset[NodeId]:
        """A node's dependency ids (args, kwargs, hidden closure captures).

        Memoised only while the node is in flight (specs are immutable, but the
        memo is per-frontier state and is dropped at completion; a later
        ``_rematerialize`` of an evicted value simply recomputes it).
        """
        cached = self._deps_memo.get(nid)
        if cached is None:
            cached = frozenset(Expander.dependencies(self.table.nodes[nid]))
            self._deps_memo[nid] = cached
        return cached

    # ── Registration / firing ─────────────────────────────────────────────────

    def register(self, nid: NodeId, deps=None) -> bool:
        """Wire one node into the graph; return True if it is ready now.

        Readiness is gated only on whether a dependency has *completed* (a
        monotonic fact), never on whether its value is currently resident:
        values may be evicted and rematerialised on demand, so gating on
        residency could wait forever. ``deps`` overrides the spec-derived
        dependency set (used by the runtime-spliced sequence node, whose deps
        are its loop's bodies).
        """
        self.incomplete.add(nid)
        self.registered_total += 1
        unmet = 0
        for dep in (self.deps(nid) if deps is None else deps):
            self.consumers[dep] = self.consumers.get(dep, 0) + 1
            if dep in self.incomplete:
                unmet += 1
                self._dependents[dep].append(nid)
        self.pending[nid] = unmet
        return unmet == 0

    def complete_trivial(self, nid: NodeId) -> None:
        """Mark a never-registered node (constant/closure) completed.

        Trivial nodes are completed *eagerly at discovery* instead of taking a
        full ready-queue turn — in loop-heavy plans they are roughly half of
        all completions, and none of them needs a worker.
        """
        self.registered_total += 1
        self.table.completed.add(nid)

    def await_one(self, nid: NodeId, dep: NodeId) -> None:
        """Make an already-registered node wait for one extra dependency.

        Used by loop splicing: the loop node (still on the frontier after its
        expansion turn) re-fires when its spliced sequence completes. The
        caller is responsible for any value hold on ``dep``.
        """
        self.pending[nid] = 1
        self._dependents[dep].append(nid)

    def on_complete(self, nid: NodeId, release_inputs: bool = True) -> list[NodeId]:
        """Record completion; return newly-fired dependents. O(degree).

        Releases the node's hold on its inputs (unless the caller manages the
        holds itself, e.g. closures whose captures stay pinned for their loop),
        fires dependents whose last unmet dependency this was, then drops all
        per-node state — after this call the node costs the scheduler nothing.
        """
        self.incomplete.discard(nid)
        if release_inputs:
            for dep in self.deps(nid):
                self.release(dep)
        fired: list[NodeId] = []
        for child in self._dependents.pop(nid, ()):
            self.pending[child] -= 1
            if self.pending[child] == 0:
                fired.append(child)
        self.pending.pop(nid, None)
        self._deps_memo.pop(nid, None)
        return fired

    def complete_cone(self, members_topo, member_set: frozenset[NodeId],
                       interiors: frozenset[NodeId]) -> None:
        """Batch-drop scheduling/refcount state for a fusion cone's INTERIOR
        members (``engine/fusion.py``) — those whose value is deliberately
        never materialized because every one of their consumers is itself a
        cone member, resolving in this same synchronous batch.

        Exits are NOT touched here: the caller finishes each exit through
        the normal ``on_complete``/``_finish`` path, whose own
        release-my-deps loop already correctly releases any interior it
        depends on (see below — by the time it runs, that release is a
        harmless no-op).

        An interior's dependency on ANOTHER cone member needs no per-edge
        release/eviction check: both ends of that edge resolve in this same
        batch, so "does this drop to zero, should the value be evicted" is
        moot (the value never entered the live tier). Only a dependency on
        something OUTSIDE the cone still needs the normal ``release()`` —
        that value's lifetime is not otherwise accounted for here.

        An interior's OWN ``consumers`` entry is dropped unconditionally —
        never decremented-and-checked per edge. By the definition of
        "interior" (``FusionPlanner.plan``), every one of its registered
        consumers is itself a cone member completing in this batch, so the
        count is guaranteed to reach exactly zero regardless of how many
        internal edges point to it: one dict pop replaces what would
        otherwise be one ``release()`` call per incoming edge — this is the
        actual saving over calling ``on_complete`` once per interior member
        (measured to dominate per-node cost; see
        doc/dev/dynamic-scheduler/frontier-scheduler.md, "Semantic queueing").
        """
        for member in members_topo:
            if member not in interiors:
                continue
            self.incomplete.discard(member)
            for dep in self.deps(member):
                if dep not in member_set:
                    self.release(dep)
            self.pending.pop(member, None)
            self._dependents.pop(member, None)
            self._deps_memo.pop(member, None)
            self.consumers.pop(member, None)

    # ── Value lifetime ────────────────────────────────────────────────────────

    def pin(self, nid: NodeId) -> None:
        """Add one consumer reference (a hold) to a value."""
        self.consumers[nid] = self.consumers.get(nid, 0) + 1

    def release(self, nid: NodeId) -> None:
        """Drop one consumer reference; evict the value on the last release.

        Eviction at worst costs a reload (disk tier) or a recompute — a value
        never survives past its last consumer, which is what keeps the live
        tier flat under wide fan-out. ``protected`` (goal ids) is exempt.
        """
        remaining = self.consumers.get(nid, 0)
        if remaining <= 0:
            return
        remaining -= 1
        if remaining:
            self.consumers[nid] = remaining
        else:
            del self.consumers[nid]  # drop the entry: state is frontier-only
            if nid not in self.protected:
                self.table.evict(nid)
