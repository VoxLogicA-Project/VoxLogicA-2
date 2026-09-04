"""FusionPlanner invariants (engine/fusion.py) — pure, synchronous, no asyncio.

Builds real (not mocked) NodeTable/DependencyGraph state by hand to exercise
``plan()`` directly, mirroring the setup style of test_memory_backpressure.py.
The key invariant under test throughout: growth only absorbs a consumer that
is "ripe within the hypothetical cone" (every OTHER dependency already
completed) — never a node the live scheduler has already fired onto the
ready queue (pending == 0), and never across a non-elementwise or shape
boundary. See doc/specs/semantic-queueing-fusion.md §3 and
doc/specs/fusion-implementation-handover.md.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.arrays import PolyArray
from voxlogica.engine.fusion import FusionPlanner
from voxlogica.engine.graph import DependencyGraph
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeSpec
from voxlogica.primitives.registry import PrimitiveRegistry


def _registry() -> PrimitiveRegistry:
    reg = PrimitiveRegistry()
    reg.import_namespace("vox1")
    return reg


def _mask(shape, value: int) -> PolyArray:
    arr = np.full(shape, value, dtype=np.uint8)
    return PolyArray.from_sitk(sitk.GetImageFromArray(arr))


def _complete(table: NodeTable, graph: DependencyGraph, node_id: str, node: NodeSpec, value) -> None:
    """Register + immediately complete a node — a stand-in "already done" input.

    Must go through ``graph.on_complete`` (not just ``table.completed.add``) —
    that is what actually drops the node from ``graph.incomplete``, which is
    what ``register``'s readiness/unmet-dependency count reads.
    """
    table.nodes[node_id] = node
    graph.register(node_id)
    table.set_value(node_id, value)
    table.completed.add(node_id)
    graph.on_complete(node_id)


@pytest.mark.unit
def test_plan_grows_a_ripe_two_member_cone() -> None:
    """not(a) -> and(not_a, b): seeding at not_a must absorb and_node, since
    and_node's only unmet dependency (not_a) is the seed itself."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()

    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 3, 4), 1))
    _complete(table, graph, "b", NodeSpec(kind="constant", operator="constant"), _mask((2, 3, 4), 1))

    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    ready = graph.register("not_a")
    assert ready, "not_a's only dep (a) is already completed"

    table.nodes["and_node"] = NodeSpec(kind="primitive", operator="vox1.and", args=("not_a", "b"))
    ready = graph.register("and_node")
    assert not ready, "and_node still waits on not_a"
    assert graph.pending["and_node"] == 1
    # A downstream consumer outside the elementwise set (e.g. a print goal) —
    # this is what makes and_node an EXIT (real DAGs never leave a non-goal
    # node with zero consumers; something always reads it, up to a goal).
    table.nodes["print_goal"] = NodeSpec(kind="primitive", operator="default.print", args=("and_node",))
    graph.register("print_goal")

    planner = FusionPlanner(registry)
    cone = planner.plan("not_a", graph=graph, table=table, goals=set(), cap=64)

    assert cone is not None
    assert set(cone.members_topo) == {"not_a", "and_node"}
    assert cone.members_topo[0] == "not_a", "seed must be first in topo order"
    assert cone.inputs == frozenset({"a", "b"})
    assert cone.exits == frozenset({"and_node"}), "and_node has an external consumer -> exit"
    assert cone.interiors == frozenset({"not_a"})
    # Growth must claim every member — both now unclaimable a second time.
    assert not table.is_claimable("not_a")
    assert not table.is_claimable("and_node")


@pytest.mark.unit
def test_plan_returns_none_for_non_elementwise_seed() -> None:
    """A seed with no ElementwiseSpec (e.g. a structural op) never fuses."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    table.nodes["seq"] = NodeSpec(kind="primitive", operator="default.sequence", args=())
    graph.register("seq")

    planner = FusionPlanner(registry)
    assert planner.plan("seq", graph=graph, table=table, goals=set(), cap=64) is None


@pytest.mark.unit
def test_plan_returns_none_without_a_ripe_partner() -> None:
    """A lone elementwise node with no absorbable consumer yields no cone
    (< 2 members is not worth fusing) — the caller takes the normal path."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")

    planner = FusionPlanner(registry)
    assert planner.plan("not_a", graph=graph, table=table, goals=set(), cap=64) is None
    assert table.is_claimable("not_a"), "an unfused seed must not be left claimed"


@pytest.mark.unit
def test_plan_stops_growth_at_a_non_elementwise_consumer() -> None:
    """not(a) feeding a structural/non-elementwise consumer must not absorb it
    — the cone boundary is exactly the elementwise/sitk-primitive line."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")
    # dt (distance transform) is real sitk work, never elementwise.
    table.nodes["dt_node"] = NodeSpec(kind="primitive", operator="vox1.dt", args=("not_a",))
    graph.register("dt_node")

    planner = FusionPlanner(registry)
    assert planner.plan("not_a", graph=graph, table=table, goals=set(), cap=64) is None


@pytest.mark.unit
def test_plan_stops_growth_at_shape_mismatch() -> None:
    """A consumer whose OTHER external input has a different resident shape
    must not be absorbed — cones only span one voxel domain (§3.1)."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 3, 4), 1))
    # b has a DIFFERENT shape than a.
    _complete(table, graph, "b", NodeSpec(kind="constant", operator="constant"), _mask((9, 9, 9), 1))

    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")
    table.nodes["and_node"] = NodeSpec(kind="primitive", operator="vox1.and", args=("not_a", "b"))
    graph.register("and_node")

    planner = FusionPlanner(registry)
    cone = planner.plan("not_a", graph=graph, table=table, goals=set(), cap=64)
    assert cone is None, "shape mismatch must cut growth, leaving < 2 members"
    assert table.is_claimable("not_a")


@pytest.mark.unit
def test_goal_member_is_always_an_exit_never_interior() -> None:
    """A cone member that is itself a query goal must never be classified
    interior, even if every one of its graph consumers is in-cone — a goal's
    value must remain independently addressable/persistable."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    _complete(table, graph, "b", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")
    table.nodes["and_node"] = NodeSpec(kind="primitive", operator="vox1.and", args=("not_a", "b"))
    graph.register("and_node")

    planner = FusionPlanner(registry)
    cone = planner.plan("not_a", graph=graph, table=table, goals={"not_a"}, cap=64)

    assert cone is not None
    assert "not_a" in cone.exits, "goal-status forces exit classification"
    assert "not_a" not in cone.interiors


@pytest.mark.unit
def test_plan_respects_the_cap() -> None:
    """Growth stops at ``cap`` members even if more ripe consumers exist."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")
    prev = "not_a"
    for i in range(10):
        node_id = f"not_{i}"
        table.nodes[node_id] = NodeSpec(kind="primitive", operator="vox1.not", args=(prev,))
        graph.register(node_id)
        prev = node_id

    planner = FusionPlanner(registry)
    cone = planner.plan("not_a", graph=graph, table=table, goals=set(), cap=5)
    assert cone is not None
    assert len(cone) <= 5


@pytest.mark.unit
def test_stage_pinned_node_is_never_classified_interior() -> None:
    """Regression for a real deadlock found while implementing Phase 2 leg 1.

    ``graph.pin()`` (used by ``LoopAdmission._run_job`` for a runtime loop
    body's "stage pin", held until the loop's sequence node registers as its
    real consumer) bumps ``consumers`` WITHOUT adding a ``_dependents``
    entry. A classification rule that only inspects ``_dependents`` sees a
    stage-pinned node as having zero consumers and elides it as interior —
    but the sequence node the pin exists to protect has not registered yet
    (it can't: the sequence only registers once every body in the loop has
    been admitted, which may be long after this one body's cone is planned).
    The eventual sequence then waits forever for a value that was computed
    but never materialized: a real hang, not merely a wrong result. A node
    with ANY hold unaccounted for by a registered dependent edge must always
    be treated as an exit.
    """
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")
    table.nodes["body_root"] = NodeSpec(kind="primitive", operator="vox1.not", args=("not_a",))
    graph.register("body_root")
    # The stage pin: a hold with no _dependents entry (graph.pin() only
    # bumps consumers — exactly what LoopAdmission does before the loop's
    # sequence node exists to be a real registered dependent).
    graph.pin("body_root")

    planner = FusionPlanner(registry)
    cone = planner.plan("not_a", graph=graph, table=table, goals=set(), cap=64)

    assert cone is not None
    assert "body_root" in cone.exits, "a stage-pinned node must never be interior"
    assert "body_root" not in cone.interiors


@pytest.mark.unit
def test_claimed_members_never_have_pending_zero() -> None:
    """Cone members must never be nodes the live scheduler has already fired
    onto the ready queue (pending == 0) — growth only reaches nodes ripe
    WITHIN the hypothetical cone, whose real ``pending`` count (against the
    live graph) is still >= 1 because the cone hasn't executed yet."""
    table = NodeTable(backend=None)
    graph = DependencyGraph(table)
    registry = _registry()
    _complete(table, graph, "a", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    _complete(table, graph, "b", NodeSpec(kind="constant", operator="constant"), _mask((2, 2, 2), 1))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    graph.register("not_a")
    table.nodes["and_node"] = NodeSpec(kind="primitive", operator="vox1.and", args=("not_a", "b"))
    graph.register("and_node")

    planner = FusionPlanner(registry)
    cone = planner.plan("not_a", graph=graph, table=table, goals=set(), cap=64)

    assert cone is not None
    for member in cone.members_topo[1:]:  # exclude the seed, which IS pending==0 (that's why it was popped)
        assert graph.pending.get(member, 1) >= 1
