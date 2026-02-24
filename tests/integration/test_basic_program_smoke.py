from __future__ import annotations

import pytest

from voxlogica.execution import ExecutionEngine


@pytest.mark.integration
def test_basic_program_smoke_execution(reduce_from_text, sample_dataset_file):
    program = """
let f(x,y) = x + y
let y = f(2,3)
print "sum" y
print "rows" load("__DATASET__")
"""
    program = program.replace("__DATASET__", str(sample_dataset_file))
    workplan = reduce_from_text(program)

    engine = ExecutionEngine()
    result = engine.execute_workplan(workplan, strategy="strict")

    assert result.success
    assert len(workplan.goals) == 2
