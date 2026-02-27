from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.storage import MaterializationStore, SQLiteResultsDatabase


@pytest.mark.unit
def test_async_persistence_flushes_after_compute(tmp_path: Path) -> None:
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    store = MaterializationStore(backend=db, read_through=False, write_through=True)
    try:
        store.put("node-async", 123, metadata={"source": "runtime"})
        meta = store.metadata("node-async")
        assert meta.get("source") == "runtime"
        assert meta.get("persisted") in {"pending", True}
        assert store.flush(timeout_s=5.0) is True
        assert db.has("node-async") is True
    finally:
        store.close()
        db.close()

