from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import NoCacheStorageBackend


@pytest.mark.unit
def test_lazy_subsequence_slices_dir_results(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for index in range(8):
        (tmp_path / f"case_{index}.txt").write_text("x", encoding="utf-8")

    program = parse_program_content(
        f"""
paths = subsequence(dir("{tmp_path}", "*.txt", false, false), 1, 4)
only = for p in paths do p
print "first" index(only, 0)
print "last" index(only, 2)
"""
    )
    workplan = reduce_program(program)
    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True)
    result = engine.execute_workplan(workplan)

    assert result.success is True
    output = capsys.readouterr().out
    assert "first=case_1.txt" in output
    assert "last=case_3.txt" in output
