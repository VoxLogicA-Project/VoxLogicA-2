from __future__ import annotations

import dask.bag as db

from voxlogica.execution_strategy.parallel import ParallelExecutionStrategy
from voxlogica.parser import parse_program_content
from voxlogica.primitives.default import dask_map
from voxlogica.reducer import reduce_program


def test_parallel_closure_resolves_captured_user_function():
    program = """
process_path(path) = path + 1
result = for path in range(0, 3) do process_path(path)
print "result" result
"""
    workplan = reduce_program(parse_program_content(program))
    plan = workplan.to_symbolic_plan()
    strategy = ParallelExecutionStrategy(workplan.registry)
    prepared = strategy.compile(plan)

    result = strategy.run(prepared)

    assert result.success
    assert result.failed_operations == {}
    assert prepared.values[plan.goals[0].id] == [1.0, 2.0, 3.0]


def test_parallel_map_materializes_result_without_leaking_dask_bag():
    program = """
increment(path) = path + 1
result = map(increment, range(0, 3))
print "result" result
"""
    workplan = reduce_program(parse_program_content(program))
    plan = workplan.to_symbolic_plan()
    strategy = ParallelExecutionStrategy(workplan.registry)
    prepared = strategy.compile(plan)

    result = strategy.run(prepared)
    value = prepared.values[plan.goals[0].id]

    assert result.success
    assert result.failed_operations == {}
    assert value == [1.0, 2.0, 3.0]
    assert isinstance(value, list)


def test_dask_map_accepts_positional_closure_argument():
    def increment(value):
        return value + 1

    result = dask_map.execute(**{"0": db.from_sequence([1, 2]), "1": increment})

    assert isinstance(result, db.Bag)
    assert result.compute(scheduler="threads") == [2, 3]
