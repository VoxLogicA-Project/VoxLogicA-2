from __future__ import annotations

import pytest

from voxlogica.execution import ExecutionEngine


@pytest.mark.regression
def test_nested_let_shadowing_executes_correctly(reduce_from_text):
    program = """
let result = let x = 1 in let x = x + 10 in x + 5
print "result" result
"""
    workplan = reduce_from_text(program)
    plan = workplan.to_symbolic_plan()
    goal_id = plan.goals[0].id

    engine = ExecutionEngine()
    prepared = engine.compile_plan(workplan, strategy="dask")
    page = engine.page(prepared, goal_id, offset=0, limit=1, strategy="dask")

    assert page.items == [16.0]


@pytest.mark.regression
def test_let_expression_scope_does_not_leak(reduce_from_text):
    program = """
let outer = 5
let result = let x = outer + 1 in x + 2
let final = outer + result
print "final" final
"""
    workplan = reduce_from_text(program)
    plan = workplan.to_symbolic_plan()
    goal_id = plan.goals[0].id

    engine = ExecutionEngine()
    prepared = engine.compile_plan(workplan, strategy="dask")
    page = engine.page(prepared, goal_id, offset=0, limit=1, strategy="dask")

    assert page.items == [13.0]
