from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxlogica.storage import MaterializationStore, SQLiteResultsDatabase


@pytest.mark.unit
def test_persisted_value_is_evicted_from_ram_and_reloaded_from_disk(tmp_path: Path) -> None:
    """Over capacity, a durable value is dropped from RAM and reloaded from disk."""
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    store = MaterializationStore(backend=db, read_through=True, write_through=True, memory_capacity=2)
    try:
        a0 = np.arange(3, dtype=np.float32)
        a1 = np.arange(4, dtype=np.float32)
        a2 = np.arange(5, dtype=np.float32)

        store.put("n0", "e", [], a0, metadata={"source": "runtime"})
        store.put("n1", "e", [], a1, metadata={"source": "runtime"})
        assert store.flush(timeout_s=5.0) is True  # n0, n1 now durable on disk

        # Third value pushes the cache over capacity; the LRU victim n0 is
        # durable, so it is evicted from the memory tier.
        store.put("n2", "e", [], a2, metadata={"source": "runtime"})
        assert "n0" not in store._memory
        assert "n1" in store._memory and "n2" in store._memory

        # The evicted value is still retrievable (reloaded from tier 2).
        reloaded = store.get("n0")
        assert reloaded is not None
        assert np.array_equal(reloaded, a0)
    finally:
        store.close()
        db.close()


@pytest.mark.unit
def test_unpersisted_value_is_not_evicted(tmp_path: Path) -> None:
    """A value that is not yet durable must stay in RAM even past capacity."""
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    store = MaterializationStore(backend=db, read_through=True, write_through=True, memory_capacity=1)
    try:
        a0 = np.arange(3, dtype=np.float32)
        a1 = np.arange(4, dtype=np.float32)
        # Persistence is async and not flushed: nothing is durable yet, so the
        # cache may exceed capacity rather than risk a recompute.
        store.put("n0", "e", [], a0, metadata={"source": "runtime"})
        store.put("n1", "e", [], a1, metadata={"source": "runtime"})
        assert "n0" in store._memory
        assert "n1" in store._memory
    finally:
        store.close()
        db.close()


@pytest.mark.unit
def test_no_backend_keeps_all_values_in_ram() -> None:
    """With no backend (--no-cache) nothing is evicted, so dedup is preserved."""
    store = MaterializationStore(backend=None, read_through=False, write_through=False, memory_capacity=1)
    a = np.arange(3, dtype=np.float32)
    b = np.arange(4, dtype=np.float32)
    store.put("a", "e", [], a, metadata={"source": "runtime"})
    store.put("b", "e", [], b, metadata={"source": "runtime"})
    assert np.array_equal(store.get("a"), a)
    assert np.array_equal(store.get("b"), b)
