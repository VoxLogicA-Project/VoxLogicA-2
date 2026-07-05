"""The persistent store is a bounded, cost-aware (GreedyDual-Size) cache.

Values are regenerable from lineage, so eviction only ever costs a recompute.
The eviction key is ``clock + compute_ms / bytes``: a small value that was
expensive to compute is kept over a large value that was cheap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_expensive_small_value_survives_cheap_large_ones(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    budget = 5_000_000  # 5 MB
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"), max_bytes=budget)
    try:
        # One small but very expensive result (think: a hard-won model).
        precious = "%064x" % 0xABCDEF
        db.put_success(precious, rng.random((64, 512)), metadata={}, compute_ms=100_000.0)  # ~256 KB, 100 s
        # Many large, cheap intermediates that blow the budget.
        bulk = ["%064x" % i for i in range(12)]
        for nid in bulk:
            db.put_success(nid, rng.random((256, 1024)), metadata={}, compute_ms=1.0)  # ~1 MB, ~free
        assert db._payload_bytes <= budget
        assert db.has(precious), "the small expensive value must be kept"
        assert sum(1 for nid in bulk if db.has(nid)) < len(bulk), "cheap large values must be evicted"
    finally:
        db.close()


@pytest.mark.unit
def test_total_bytes_stay_under_budget_and_are_tracked(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(1)
    budget = 4_000_000
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"), max_bytes=budget)
    try:
        for i in range(12):
            db.put_success("%064x" % i, rng.random((256, 1024)), metadata={}, compute_ms=float(i))
        assert db._payload_bytes <= budget
        on_disk = int(db._connection.execute("SELECT COALESCE(SUM(payload_bytes),0) FROM results").fetchone()[0])
        assert on_disk == db._payload_bytes
        assert db.stats()["evictions"] > 0
    finally:
        db.close()


@pytest.mark.unit
def test_cache_unbounded_when_budget_zero(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(2)
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"), max_bytes=0)
    try:
        for i in range(6):
            db.put_success("%064x" % i, rng.random((256, 1024)), metadata={}, compute_ms=1.0)
        assert all(db.has("%064x" % i) for i in range(6))  # nothing evicted
    finally:
        db.close()
