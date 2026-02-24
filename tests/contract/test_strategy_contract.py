from __future__ import annotations

import pytest

from voxlogica.execution_strategy.dask import DaskExecutionStrategy
from voxlogica.execution_strategy.strict import StrictExecutionStrategy


@pytest.mark.contract
def test_strict_and_dask_produce_equivalent_goal_results(reduce_from_text):
    program = """
let inc(x)=x+1
print "out" for x in range(0,6) do inc(x)
"""
    workplan = reduce_from_text(program)
    plan = workplan.to_symbolic_plan()
    assert plan.goals, "expected at least one goal"

    goal_id = plan.goals[0].id

    strict = StrictExecutionStrategy(workplan.registry)
    dask = DaskExecutionStrategy(workplan.registry)

    strict_prepared = strict.compile(plan)
    dask_prepared = dask.compile(plan)

    strict_items = strict.page(strict_prepared, goal_id, offset=0, limit=100).items
    dask_items = dask.page(dask_prepared, goal_id, offset=0, limit=100).items

    assert strict_items == dask_items
