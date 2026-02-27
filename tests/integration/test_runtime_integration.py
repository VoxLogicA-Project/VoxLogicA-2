from __future__ import annotations

from itertools import islice
from pathlib import Path
import json

import pytest

from voxlogica.execution import ExecutionEngine


@pytest.mark.integration
def test_for_loop_executes_on_dask_strategy(reduce_from_text):
    program = """
let inc(x)=x+1
print "out" for x in range(0,5) do inc(x)
"""
    workplan = reduce_from_text(program)
    plan = workplan.to_symbolic_plan()

    engine = ExecutionEngine()
    prepared = engine.compile_plan(workplan, strategy="dask")
    goal_id = plan.goals[0].id

    page = engine.page(prepared, goal_id, offset=0, limit=10, strategy="dask")
    assert page.items == [1.0, 2.0, 3.0, 4.0, 5.0]


@pytest.mark.integration
def test_stream_and_page_without_full_materialization(reduce_from_text, sample_dataset_file: Path):
    program = f'print "rows" load("{sample_dataset_file}")'
    workplan = reduce_from_text(program)
    plan = workplan.to_symbolic_plan()
    goal_id = plan.goals[0].id

    engine = ExecutionEngine()
    prepared = engine.compile_plan(workplan, strategy="dask")

    first_two_chunks = list(islice(engine.stream(prepared, goal_id, chunk_size=2, strategy="dask"), 2))
    assert first_two_chunks == [["alpha", "beta"], ["gamma", "delta"]]

    page = engine.page(prepared, goal_id, offset=1, limit=2, strategy="dask")
    assert page.items == ["beta", "gamma"]


@pytest.mark.integration
def test_save_goal_writes_output(reduce_from_text, tmp_path: Path):
    output_path = tmp_path / "result.json"
    program = f'save "{output_path}" for x in range(0,3) do x+1'
    workplan = reduce_from_text(program)

    engine = ExecutionEngine()
    result = engine.execute_workplan(workplan, strategy="dask")

    assert result.success
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == [1.0, 2.0, 3.0]
