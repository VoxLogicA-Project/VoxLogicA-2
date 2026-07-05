"""An oversubscribed working set must complete without eviction⇄recompute thrash.

Runs a wide independent fan-out whose produced values dwarf the cache cap, and
asserts the run (a) completes, (b) keeps the resident working set bounded (well
below the total produced), and (c) does not thrash (recompute count stays small,
not proportional to the work). Metrics come from the engine's run summary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

# 60 independent cases, each building a distinct image plus a shared one, then
# reducing to small stats. Every case is demanded (no slicing), so the whole
# fan-out runs; the images total far more than the tiny live budget below.
PROGRAM = """
import "simpleitk"
import "geom"
import "arrays"
shared = blank(600, 600, 1.0)
work(g) = array_stats(Add(blank(600, 600, g), shared))
cases = range(0, 60)
out = for g in cases do work(g)
print "r" out
"""


@pytest.mark.unit
def test_oversubscribed_working_set_completes_without_thrash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("SimpleITK")
    monkeypatch.setenv("VOXLOGICA_MAX_LIVE_GB", "0.008")  # 8 MB live budget
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "c.db"), max_bytes=8 * 1024 * 1024)
    try:
        result = ExecutionEngine(storage_backend=backend, use_engine=True).execute_workplan(
            reduce_program(parse_program_content(PROGRAM))
        )
    finally:
        backend.close()

    assert result.success, result.failed_operations
    summary = result.cache_summary
    # Resident working set stayed bounded — nowhere near the ~85 MB of images
    # produced across the 60 cases (depth-first + eviction + admission together).
    assert summary["peak_live_mb"] < 45, summary
    # And it did not thrash: recomputes are bounded, not proportional to the work.
    assert summary["recomputes"] < 30, summary
    assert "r=" in capsys.readouterr().out  # produced results
