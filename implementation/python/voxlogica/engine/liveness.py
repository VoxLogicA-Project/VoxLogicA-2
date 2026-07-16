"""Liveness for the disk cache's eviction preference — O(1), no traversal.

The persistent tier prefers to evict values nothing will ask for again. The
old engine recomputed that set with a transitive dependency walk from every
incomplete node — a tracing GC over the whole open graph, every 128
completions, O(plan) each time; at production scale it consumed >half of all
event-loop cycles (the single-core collapse). But the engine already maintains
the same information incrementally, so tracing is replaced by the
reference-counting dual:

    live(n) ≡ n is on the frontier (incomplete)
            ∨ n's value has unrun consumers (consumer refcount > 0)
            ∨ n is staged by an open loop (reduced but not yet admitted)
            ∨ n is a goal of an unsettled query

Instead of periodically *pushing* a materialized set, the backend is handed
this predicate and evaluates it per eviction candidate — the steady-state cost
drops from O(plan)/128-completions to zero (eviction only happens when the
disk cache exceeds its budget, which is rare).

APPROXIMATION, AND WHY IT IS SAFE: interior nodes of not-yet-admitted loop
bodies are not individually marked (their subtrees have no scheduler state
yet — that absence is the whole point). Values *shared* across bodies are
covered anyway: loop captures stay pinned (consumers > 0) for the loop's whole
unroll. A persisted interior one-shot value evicted early costs one reload or
recompute when its body is admitted. Liveness here is an eviction *preference*
— never a correctness gate (every value is regenerable from lineage).

THREADING: the predicate runs on the persister thread while the event loop
mutates these sets. Each check is a single dict/set membership — atomic under
the GIL — and a stale answer merely shifts an eviction preference, so no lock
is taken (same tolerance the old snapshot push relied on).
"""

from __future__ import annotations

from typing import Callable

from voxlogica.engine.graph import DependencyGraph
from voxlogica.lazy.ir import NodeId


class LivenessProbe:
    """The incremental live predicate handed to the storage backend."""

    def __init__(self, graph: DependencyGraph):
        self._graph = graph
        self.staged: set[NodeId] = set()        # reduced-not-yet-admitted loop bodies
        self.unsettled_goals: set[NodeId] = set()

    def is_live(self, nid: NodeId) -> bool:
        graph = self._graph
        return (nid in graph.incomplete
                or nid in graph.consumers          # >0 by construction (dropped at 0)
                or nid in self.staged
                or nid in self.unsettled_goals)

    def install(self, backend) -> None:
        """Hand the probe to a backend that supports it (else keep silent).

        Backends without ``set_live_probe`` simply evict by cost/recency alone —
        graceful degradation, matching --no-cache behaviour.
        """
        setter: Callable | None = getattr(backend, "set_live_probe", None)
        if setter is not None:
            setter(self.is_live)
