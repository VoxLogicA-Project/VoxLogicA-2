"""Engine disk-caching round-trip: async persistence must be correct and reusable.

Persistence runs on a background thread and must not change results; a second
run over a warm cache must produce identical output and skip recomputation of
already-durable values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.execution import ExecutionEngine
from voxlogica.storage import SQLiteResultsDatabase

PROGRAM = """
xs = range(0, 8)
sq = for x in xs do x * x
print "r" sq
"""


def _run_engine(db_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    backend = SQLiteResultsDatabase(db_path=str(db_path))
    try:
        result = ExecutionEngine(storage_backend=backend, use_engine=True).execute_workplan(
            reduce_program(parse_program_content(PROGRAM))
        )
        assert result.success is True
    finally:
        backend.close()
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("r="):
            return line
    raise AssertionError("no 'r=' output produced")


@pytest.mark.unit
def test_engine_cold_and_warm_runs_agree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "results.db"
    cold = _run_engine(db, capsys)  # cold: computes and persists
    warm = _run_engine(db, capsys)  # warm: reuses the durable cache
    assert cold == "r=[0.0, 1.0, 4.0, 9.0, 16.0, 25.0, 36.0, 49.0]"
    assert warm == cold


@pytest.mark.unit
def test_engine_persists_values_to_backend(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "results.db"
    _run_engine(db, capsys)
    # The background writer must have flushed durable rows before the run returned.
    backend = SQLiteResultsDatabase(db_path=str(db))
    try:
        count = backend._connection.execute(
            "SELECT count(*) FROM results WHERE status = 'materialized'"
        ).fetchone()[0]
    finally:
        backend.close()
    assert count > 0
