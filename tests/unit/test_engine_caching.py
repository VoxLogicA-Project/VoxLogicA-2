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
def test_warm_run_reuses_runtime_expanded_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`for x in range(...)` expands into nodes at runtime; a warm re-run must
    reuse those cached results through the same machinery as any other node.

    Reuse is *worth-it gated* (EngineConfig.persist_min_compute_ms): a value
    cheaper to recompute than to serialize+write+reload is deliberately not
    persisted, so a warm run may recompute sub-threshold trivia but must never
    recompute anything expensive. VOXLOGICA_PERSIST_MIN_MS=0 restores
    persist-everything, under which a warm run recomputes nothing at all —
    both regimes are pinned here."""
    from voxlogica.engine import executor as executor_module

    calls: list[str] = []
    original = executor_module.Executor._compute

    def counting_compute(self, table, node_id):  # noqa: ANN001
        calls.append(node_id)
        return original(self, table, node_id)

    monkeypatch.setattr(executor_module.Executor, "_compute", counting_compute)

    # Default regime: only sub-threshold (cheap) nodes may be recomputed warm.
    db = tmp_path / "results.db"
    _run_engine(db, capsys)
    cold_calls = len(calls)
    calls.clear()
    _run_engine(db, capsys)
    cheap_warm_calls = len(calls)
    assert cold_calls > 0, "cold run should have computed the expanded nodes"
    assert cheap_warm_calls <= cold_calls, "warm run must reuse at least the structural cut"

    # Persist-everything regime: byte-for-byte the old guarantee.
    monkeypatch.setenv("VOXLOGICA_PERSIST_MIN_MS", "0")
    calls.clear()
    db_all = tmp_path / "results-all.db"
    _run_engine(db_all, capsys)
    assert len(calls) > 0
    calls.clear()
    _run_engine(db_all, capsys)
    assert len(calls) == 0, f"warm run recomputed {len(calls)} nodes instead of reusing the cache"


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
