"""engine/parallelism.py: work/span (Brent) bound on a plan.

This is the control that tells "the engine is leaving parallelism unused" apart
from "this program has no parallelism to use" -- indistinguishable from a
speedup number alone. See doc/dev/scaling-test-design.md sec 3.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.parallelism import analyze
from voxlogica.lazy.ir import NodeSpec, SymbolicPlan


def _plan(edges: dict[str, list[str]], operators: dict[str, str] | None = None
          ) -> SymbolicPlan:
    """Build a plan from {node: [deps]}."""
    operators = operators or {}
    nodes = {
        node: NodeSpec(kind="operation", operator=operators.get(node, "op"),
                       args=tuple(deps))
        for node, deps in edges.items()
    }
    return SymbolicPlan(nodes=nodes)


@pytest.mark.unit
def test_independent_nodes_are_fully_parallel():
    """N nodes, no dependencies: work N, span 1, so any scheduler could use N
    workers."""
    report = analyze(_plan({"a": [], "b": [], "c": [], "d": []}))
    assert report.work == 4
    assert report.span == 1
    assert report.max_speedup == 4.0


@pytest.mark.unit
def test_a_chain_admits_no_parallelism_at_all():
    """The case that makes 'scales 24x on every analysis' impossible in
    principle: a serial pipeline has work == span, bound 1.0, and no engine can
    improve on it."""
    report = analyze(_plan({"a": [], "b": ["a"], "c": ["b"], "d": ["c"]}))
    assert report.work == 4
    assert report.span == 4
    assert report.max_speedup == 1.0
    assert report.critical_path_nodes == 4


@pytest.mark.unit
def test_diamond_span_follows_the_longest_branch():
    #      a
    #     / \
    #    b   c
    #     \ /   (d depends on both; e extends the c branch)
    #      d
    report = analyze(_plan({
        "a": [], "b": ["a"], "c": ["a"], "e": ["c"], "d": ["b", "e"],
    }))
    assert report.work == 5
    assert report.span == 4  # a -> c -> e -> d
    assert report.critical_path_nodes == 4


@pytest.mark.unit
def test_weighted_costs_change_which_branch_is_critical():
    """Unweighted node counts can pick the wrong critical path when operator
    costs differ by orders of magnitude (a distance transform vs a scalar add),
    which is why callers may supply measured costs."""
    edges = {"a": [], "cheap1": ["a"], "cheap2": ["cheap1"], "pricey": ["a"]}
    ops = {"a": "load", "cheap1": "add", "cheap2": "add", "pricey": "dt"}
    unweighted = analyze(_plan(edges, ops))
    assert unweighted.span == 3  # a -> cheap1 -> cheap2

    weighted = analyze(_plan(edges, ops), cost={"load": 1, "add": 1, "dt": 100})
    assert weighted.span == 101  # a -> pricey now dominates
    assert weighted.work == 103


@pytest.mark.unit
def test_cost_accepts_a_callable():
    report = analyze(
        _plan({"a": [], "b": ["a"]}, {"a": "x", "b": "y"}),
        cost=lambda operator: 2.0 if operator == "y" else 1.0,
    )
    assert report.work == 3.0
    assert report.span == 3.0


@pytest.mark.unit
def test_unknown_operators_fall_back_to_unit_cost():
    report = analyze(_plan({"a": [], "b": ["a"]}), cost={"nonexistent": 5.0})
    assert report.work == 2.0


@pytest.mark.unit
def test_kwargs_dependencies_count_toward_the_span():
    """Dependencies arrive via kwargs as well as args; missing them would
    understate the critical path and overstate the bound."""
    nodes = {
        "a": NodeSpec(kind="operation", operator="op"),
        "b": NodeSpec(kind="operation", operator="op", kwargs=(("x", "a"),)),
    }
    report = analyze(SymbolicPlan(nodes=nodes))
    assert report.span == 2
    assert report.max_speedup == 1.0


@pytest.mark.unit
def test_empty_plan_is_reported_not_crashed():
    report = analyze(SymbolicPlan(nodes={}))
    assert report.nodes == 0
    assert report.max_speedup == 0.0
    assert report.efficiency_of(1.0) == 0.0


@pytest.mark.unit
def test_deep_chain_does_not_hit_the_recursion_limit():
    """A parameter sweep produces chains far deeper than CPython's limit; a
    RecursionError while analysing scalability would be absurd."""
    depth = 20_000
    edges = {"n0": []}
    for i in range(1, depth):
        edges[f"n{i}"] = [f"n{i - 1}"]
    report = analyze(_plan(edges))
    assert report.span == depth
    assert report.critical_path_nodes == depth


@pytest.mark.unit
def test_efficiency_is_measured_against_the_bound_not_the_worker_count():
    """A program allowing only 4x that achieves 3.5x is a near-perfect engine;
    scoring it against 24 workers (15%) would misattribute the program's own
    limit to the scheduler."""
    report = analyze(_plan({"a": [], "b": [], "c": [], "d": []}))
    assert report.max_speedup == 4.0
    assert report.efficiency_of(3.5) == pytest.approx(0.875)


@pytest.mark.unit
def test_useful_workers_is_capped_by_available_parallelism():
    report = analyze(_plan({"a": [], "b": ["a"], "c": ["b"]}))  # chain, bound 1
    assert report.useful_workers(24) == 1.0
