"""How much parallelism does the PROGRAM allow? (work/span analysis)

No scheduler can beat its program's critical path. Brent's bound gives
``speedup <= work / span``, where work is the total cost of all nodes and span
is the cost of the longest dependency chain. Measuring achieved speedup without
that bound conflates two entirely different situations:

- a program with work/span = 3000 that reaches 10x -- the engine is leaving
  parallelism on the table, and there is something to fix;
- a program with work/span = 4 that reaches 3.5x -- the engine is near-optimal
  and no engine work will ever make it faster.

A 4-case x 150-combination parameter sweep is of the first shape; a single-case
segmentation pipeline is close to the second. So a bare claim of the form "the
engine scales Nx" is not meaningful without naming the program, and the target
"24x on every analysis" is unattainable *by definition* for any program whose
own work/span is below 24 -- however good the engine is. The honest deliverable
is the ratio achieved/attainable.

Costs default to 1.0 per node (span = longest chain in nodes). Real per-operator
costs vary by orders of magnitude -- a `dt` distance transform against a scalar
add -- so an unweighted bound is an approximation whose direction is not
guaranteed, and callers can pass measured per-operator means to sharpen it.
The unweighted number is still decisive when it comes out very large or very
small, which is the common case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from voxlogica.lazy.ir import NodeId, SymbolicPlan


@dataclass(frozen=True)
class ParallelismReport:
    nodes: int
    work: float
    span: float
    critical_path_nodes: int

    @property
    def max_speedup(self) -> float:
        """Brent bound: the best speedup ANY scheduler could achieve."""
        if self.span <= 0:
            return 0.0
        return self.work / self.span

    def efficiency_of(self, achieved_speedup: float) -> float:
        """Achieved speedup as a fraction of what the program permits.

        This -- not speedup/worker_count -- is the number that says whether the
        engine is doing its job.
        """
        bound = self.max_speedup
        if bound <= 0:
            return 0.0
        return achieved_speedup / bound

    def useful_workers(self, workers: int) -> float:
        """Workers that could possibly be busy: min(requested, what's available)."""
        return min(float(workers), self.max_speedup)


def analyze(plan: SymbolicPlan,
            cost: Callable[[str], float] | Mapping[str, float] | None = None
            ) -> ParallelismReport:
    """Compute work, span and the resulting parallelism bound for a plan.

    Iterative (explicit stack) rather than recursive: a parameter sweep produces
    chains far deeper than CPython's recursion limit, and a RecursionError while
    *analysing* scalability would be a poor joke.
    """
    if cost is None:
        def cost_of(_operator: str) -> float:
            return 1.0
    elif callable(cost):
        cost_of = cost  # type: ignore[assignment]
    else:
        def cost_of(operator: str) -> float:
            return float(cost.get(operator, 1.0))  # type: ignore[union-attr]

    nodes = plan.nodes
    work = 0.0
    for spec in nodes.values():
        work += cost_of(spec.operator)

    # depth[n] = cost of the most expensive dependency chain ending at n.
    depth: dict[NodeId, float] = {}
    chain: dict[NodeId, int] = {}

    def deps_of(node_id: NodeId) -> tuple[NodeId, ...]:
        spec = nodes.get(node_id)
        if spec is None:
            return ()
        return tuple(spec.args) + tuple(v for _k, v in spec.kwargs)

    for root in nodes:
        if root in depth:
            continue
        # Two-phase stack walk: push a node, then revisit it once its
        # dependencies are all resolved.
        stack: list[tuple[NodeId, bool]] = [(root, False)]
        while stack:
            node_id, expanded = stack.pop()
            if expanded:
                own = cost_of(nodes[node_id].operator) if node_id in nodes else 0.0
                best = 0.0
                best_chain = 0
                for dep in deps_of(node_id):
                    if dep in depth:
                        if depth[dep] > best:
                            best = depth[dep]
                        if chain.get(dep, 0) > best_chain:
                            best_chain = chain[dep]
                depth[node_id] = best + own
                chain[node_id] = best_chain + 1
                continue
            if node_id in depth:
                continue
            stack.append((node_id, True))
            for dep in deps_of(node_id):
                if dep not in depth:
                    stack.append((dep, False))

    span = max(depth.values()) if depth else 0.0
    critical_nodes = max(chain.values()) if chain else 0
    return ParallelismReport(nodes=len(nodes), work=work, span=span,
                             critical_path_nodes=critical_nodes)
