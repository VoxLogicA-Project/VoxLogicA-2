"""An eviction is a state, not an absence, and it has to say why.

A DELETE leaves a store in which "computed, then dropped under pressure" and
"never computed" are the same state: nothing. That made a real question --
were these masks dropped by the cheap-recompute branch, or evicted after being
written? -- undecidable after the fact, because both stories end with no row.

So an evicted value keeps its row as `status='evicted'`, carrying its lineage,
its rebuild cost and the numbers that decided its fate. It stays invisible to
every lookup (that is what makes it a retriable state and not a value) and
visible to whoever asks why.
"""

from __future__ import annotations

import json

import pytest

from voxlogica.storage import EVICTED_STATUS, MATERIALIZED_STATUS, SQLiteResultsDatabase


@pytest.fixture
def store(tmp_path):
    return SQLiteResultsDatabase(tmp_path / "store.db")


def put(store, node_id: str, value, compute_ms: float = 100.0):
    store.put_success(node_id, value, metadata={"note": "original"}, compute_ms=compute_ms)


def evict(store, node_id: str, tier: str = "evicted_dead", reason: str | None = None):
    row = store._connection.execute(
        "SELECT payload_file, payload_bytes, gd_key FROM results WHERE node_id = ?", (node_id,)
    ).fetchone()
    store._evict_row(node_id, row[0], row[1], row[2], tier, reason)


def status_of(store, node_id: str) -> str | None:
    row = store._connection.execute(
        "SELECT status FROM results WHERE node_id = ?", (node_id,)).fetchone()
    return None if row is None else row[0]


def eviction_record(store, node_id: str) -> dict:
    row = store._connection.execute(
        "SELECT metadata_json FROM results WHERE node_id = ?", (node_id,)).fetchone()
    return json.loads(row[0])["eviction"]


def test_the_row_survives_the_value(store):
    put(store, "n1", b"x" * 4096)

    evict(store, "n1")

    assert status_of(store, "n1") == EVICTED_STATUS


def test_the_reason_and_the_numbers_that_decided_it_are_kept(store):
    put(store, "n1", b"x" * 4096, compute_ms=1234.5)

    evict(store, "n1", tier="evicted_live", reason="over budget, no dead value left")

    record = eviction_record(store, "n1")
    assert record["tier"] == "evicted_live"
    assert record["reason"] == "over budget, no dead value left"
    assert record["compute_ms"] == pytest.approx(1234.5)   # what a rebuild costs
    assert record["payload_bytes"] > 0                     # what keeping it cost
    assert record["at"] > 0


def test_the_original_metadata_is_not_lost(store):
    put(store, "n1", b"x" * 4096)

    evict(store, "n1")

    row = store._connection.execute(
        "SELECT metadata_json FROM results WHERE node_id = ?", ("n1",)).fetchone()
    assert json.loads(row[0])["note"] == "original"


def test_lineage_and_cost_survive(store):
    put(store, "n1", b"x" * 4096, compute_ms=77.0)

    evict(store, "n1")

    row = store._connection.execute(
        "SELECT expression_json, dependencies_json, compute_ms FROM results WHERE node_id = ?",
        ("n1",)).fetchone()
    assert row[0] is not None and row[1] is not None
    assert row[2] == pytest.approx(77.0)


def test_a_tombstone_is_invisible_to_every_lookup(store):
    put(store, "n1", b"x" * 4096)
    evict(store, "n1")

    assert store.has("n1") is False
    record = store.get_record("n1")
    assert record is None or record.status != MATERIALIZED_STATUS
    assert record is None or record.value is None


def test_recomputing_restores_the_value_and_clears_the_tombstone(store):
    put(store, "n1", b"x" * 4096)
    evict(store, "n1")

    put(store, "n1", b"y" * 4096)

    assert status_of(store, "n1") == MATERIALIZED_STATUS
    assert store.has("n1") is True
    row = store._connection.execute(
        "SELECT metadata_json FROM results WHERE node_id = ?", ("n1",)).fetchone()
    assert "eviction" not in json.loads(row[0])


def test_tombstones_are_not_counted_as_entries(store):
    put(store, "n1", b"x" * 4096)
    put(store, "n2", b"x" * 4096)
    evict(store, "n1")

    stats = store.stats()
    assert stats["entries"] == 1
    assert stats["tombstones"] == 1


def test_an_evicted_row_is_not_rescanned_for_eviction(store):
    put(store, "n1", b"x" * 4096)
    evict(store, "n1")

    scanned = store._connection.execute(store._EVICT_SCAN).fetchall()
    assert all(row[0] != "n1" for row in scanned)
