"""The persistent store is a bounded, LRU-evicting cache (values are lineage-regenerable)."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_cache_evicts_lru_under_byte_budget(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    budget = 5_000_000  # 5 MB
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"), max_bytes=budget)
    try:
        ids = []
        for i in range(12):
            nid = "%064x" % i
            db.put_success(nid, rng.random((256, 1024)), metadata={})  # ~1 MB random (incompressible)
            ids.append(nid)
        # Stayed under budget by evicting, and the total is honestly tracked.
        assert db._payload_bytes <= budget
        recomputed = int(db._connection.execute("SELECT COALESCE(SUM(payload_bytes),0) FROM results").fetchone()[0])
        assert recomputed == db._payload_bytes
        # Newest survives; some oldest were evicted.
        assert db.has(ids[-1])
        assert sum(1 for nid in ids if db.has(nid)) < len(ids)
    finally:
        db.close()


@pytest.mark.unit
def test_cache_unbounded_when_budget_zero(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(1)
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"), max_bytes=0)
    try:
        for i in range(6):
            db.put_success("%064x" % i, rng.random((256, 1024)), metadata={})
        assert all(db.has("%064x" % i) for i in range(6))  # nothing evicted
    finally:
        db.close()
