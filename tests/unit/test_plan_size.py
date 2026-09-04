"""The plan-size estimate the ETA rests on.

NOT YET VALIDATED against a real run: the only measurement so far was taken on a
laptop at load average 10-17, where the node rate swung between 30 and 130 per
second inside one run, so the resulting 214% ETA error says more about the rate
term than about the size term. These tests pin the arithmetic and the failure
modes; they do not establish that the estimate is accurate in production.
"""

import pytest

from voxlogica.engine.plan_size import PlanSizeEstimator


@pytest.fixture
def estimator():
    return PlanSizeEstimator()


def test_nothing_to_say_before_a_loop_opens(estimator):
    assert estimator.estimate(1000) is None


def test_nothing_to_say_before_a_body_is_reduced(estimator):
    estimator.open_loop("L", None, cardinality=90, registered_now=4000)
    assert estimator.estimate(4000) is None


def test_projects_a_flat_loop_from_its_reduced_bodies(estimator):
    # 90 bodies, 4000 nodes of shared setup, 10 nodes per body.
    estimator.open_loop("L", None, cardinality=90, registered_now=4000)
    estimator.note_reduced("L", 9)
    assert estimator.estimate(4090) == 4000 + 90 * 10


def test_setup_is_counted_once_not_per_body(estimator):
    """The whole point of `base`: shared work must not be multiplied."""
    estimator.open_loop("L", None, cardinality=100, registered_now=5000)
    estimator.note_reduced("L", 10)
    # 10 bodies produced 100 nodes; naive "average per body" over everything
    # registered would give (5100/10)*100 = 51,000.
    assert estimator.estimate(5100) == 5000 + 100 * 10


def test_nesting_multiplies_the_unit_count(estimator):
    """A loop inside a loop: 12 bodies each, inside 90, is 1080 units."""
    estimator.open_loop("outer", None, cardinality=90, registered_now=4000)
    estimator.note_reduced("outer", 90)
    estimator.open_loop("inner", "outer", cardinality=12, registered_now=4500)
    estimator.note_reduced("inner", 6)
    # base moved to 4500 when the deeper level appeared; 6 units cost 60 nodes.
    assert estimator.estimate(4560) == 4500 + 1080 * 10


def test_the_estimate_is_never_below_what_is_already_registered(estimator):
    estimator.open_loop("L", None, cardinality=10, registered_now=100)
    estimator.note_reduced("L", 10)
    assert estimator.estimate(9999) >= 9999


def test_a_deeper_level_rebases_rather_than_double_counting(estimator):
    """Discovering nesting must not add the outer bodies' nodes twice."""
    estimator.open_loop("outer", None, cardinality=4, registered_now=1000)
    estimator.note_reduced("outer", 4)
    first = estimator.estimate(1040)
    estimator.open_loop("inner", "outer", cardinality=5, registered_now=1040)
    estimator.note_reduced("inner", 5)
    second = estimator.estimate(1090)
    assert first == 1000 + 4 * 10
    assert second == 1040 + 20 * 10        # base rebased to 1040, 20 units
    assert second > first                   # nesting can only enlarge the plan


def test_zero_cardinality_loop_is_ignored(estimator):
    estimator.open_loop("empty", None, cardinality=0, registered_now=10)
    assert estimator.estimate(10) is None


def test_describe_exposes_the_terms(estimator):
    estimator.open_loop("L", None, cardinality=8, registered_now=100)
    estimator.note_reduced("L", 4)
    described = estimator.describe(140)
    assert described["units_total"] == 8
    assert described["units_reduced"] == 4
    assert described["base"] == 100
    assert described["estimate"] == 100 + 8 * 10


def test_incremental_unit_count_matches_a_full_scan():
    """The maintained sum must equal the scan it replaced, including a rebase.

    The scan was O(loops) per progress frame; the sum is O(1) per report. They
    can only diverge through a bookkeeping slip, which is exactly what this
    checks -- across a nesting rebase, where the qualifying set changes.
    """
    estimator = PlanSizeEstimator()

    def scan() -> int:
        return sum(loop.reduced for loop in estimator._loops.values()
                   if loop.chain == estimator._deepest)

    estimator.open_loop("outer", None, cardinality=6, registered_now=100)
    for reduced in (1, 3, 6):
        estimator.note_reduced("outer", reduced)
        assert estimator._units_reduced == scan()

    # Two sibling inner loops: the rebase drops the outer bodies from the count.
    estimator.open_loop("in_a", "outer", cardinality=4, registered_now=200)
    assert estimator._units_reduced == scan() == 0
    estimator.open_loop("in_b", "outer", cardinality=4, registered_now=210)
    for loop_id, reduced in (("in_a", 2), ("in_b", 1), ("in_a", 4), ("in_b", 4)):
        estimator.note_reduced(loop_id, reduced)
        assert estimator._units_reduced == scan()

    estimator.close_loop("in_a")
    assert estimator._units_reduced == scan() == 8


def test_closing_a_loop_twice_does_not_double_count():
    estimator = PlanSizeEstimator()
    estimator.open_loop("L", None, cardinality=5, registered_now=10)
    estimator.close_loop("L")
    estimator.close_loop("L")
    assert estimator._units_reduced == 5


def test_loop_nodes_are_reported_on_direct_dict_insertion():
    """The reducer writes into table.nodes DIRECTLY, bypassing intern().

    WorkPlan is handed the mapping and inserts through it, so a hook on intern()
    never sees the loop nodes produced while a body is being reduced -- which is
    the only moment a loop's enclosing loop is knowable. A first attempt hooked
    intern() and detected no nesting at all: every loop looked top-level, the
    projection lost the outer cardinality, and the ETA collapsed to draining the
    frontier it already knew about.
    """
    from voxlogica.engine.node_table import NodeTable
    from voxlogica.lazy.ir import NodeSpec

    table = NodeTable()
    seen: list[str] = []
    table.nodes.on_loop = seen.append

    loop = NodeSpec(kind="primitive", operator="default.for_loop", args=("seq", "body"))
    plain = NodeSpec(kind="primitive", operator="vox1.dt", args=("x",))

    table.nodes["a_loop"] = loop
    table.nodes["a_node"] = plain
    assert seen == ["a_loop"], "only loop nodes are reported"

    table.nodes["a_loop"] = loop          # re-insertion is not a new node
    assert seen == ["a_loop"]


def test_nested_cardinalities_multiply_through_the_recorded_parent():
    """6 bodies each holding 7 is 42 units, not 7."""
    estimator = PlanSizeEstimator()
    estimator.open_loop("outer", None, cardinality=6, registered_now=100)
    estimator.open_loop("inner", "outer", cardinality=7, registered_now=120)
    estimator.note_reduced("inner", 7)
    assert estimator.describe(190)["units_total"] == 42
