from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine


@pytest.mark.perf
@pytest.mark.slow
def test_large_dataset_streaming_sanity(reduce_from_text, tmp_path: Path):
    dataset = tmp_path / "large.txt"
    dataset.write_text("\n".join(str(i) for i in range(10000)) + "\n", encoding="utf-8")

    workplan = reduce_from_text(f'print "rows" load("{dataset}")')
    goal_id = workplan.goals[0].id

    engine = ExecutionEngine()
    prepared = engine.compile_plan(workplan, strategy="dask")

    first_page = engine.page(prepared, goal_id, offset=0, limit=5, strategy="dask")
    assert first_page.items == ["0", "1", "2", "3", "4"]
