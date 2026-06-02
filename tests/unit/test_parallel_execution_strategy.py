from __future__ import annotations

from voxlogica.execution_strategy.parallel import ParallelExecutionStrategy
from voxlogica.parser import parse_program_content
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
