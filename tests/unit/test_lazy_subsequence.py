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
seq_avg(seq) = /(fold + 0 seq, fold + 0 (for x in seq do 1))
print "subseq_avg" seq_avg(for x in subsequence(range(0, 8), 1, 4) do x)
print "slice_avg" seq_avg(for x in range(0, 8)[1:4] do x)
"""
    )
    workplan = reduce_program(program)
    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True)
    result = engine.execute_workplan(workplan)

    assert result.success is True
    output = capsys.readouterr().out
    assert "first=case_1.txt" in output
    assert "last=case_3.txt" in output
    assert "subseq_avg=2" in output
    assert "slice_avg=2" in output


@pytest.mark.unit
def test_seq_avg_via_fold_executes(capsys: pytest.CaptureFixture[str]) -> None:
    program = parse_program_content(
        """
        seq_avg(seq) = /(fold + 0 seq, fold + 0 (for x in seq do 1))
        print "mean" seq_avg(for x in range(0, 5) do x)
        print "slice_mean" seq_avg(for x in range(0, 10)[2:7] do x)
        print "subseq_mean" seq_avg(for x in subsequence(range(0, 10), 2, 7) do x)
        """
    )
    workplan = reduce_program(program)
    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True)
    result = engine.execute_workplan(workplan)
    assert result.success is True
    output = capsys.readouterr().out
    assert "mean=2" in output
    assert "slice_mean=4" in output
    assert "subseq_mean=4" in output
