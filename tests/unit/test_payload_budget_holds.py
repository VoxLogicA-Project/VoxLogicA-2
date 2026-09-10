"""`--cache-max-gb` must be a bound, not a suggestion.

MEASURED, and it is why this test exists. A cold sixty-case sweep launched with
`--cache-max-gb 300` wrote **690.5 GB** of payloads in 1 h 54 m -- 709,248
files, 2.3x the budget -- and took a SHARED 3.6 TB volume from 735 GB free to
42 GB. The run then died with nothing in its log, because there was no space
left to write the failure into.

The cause was one `break`. `_enforce_budget` evicts dead values first and, when
everything left is live, evicted "the cheapest live values once, then stop
(graceful degradation)". One pass is at most `_EVICT_SCAN` (128) rows, while
the persister writes at a measured ~350 MB/s: growth outran eviction without
bound. And `--sparse-cache` installs the engine's liveness probe, which answers
"live" for anything a running sweep might still read -- so on the workload the
cap exists for, the live branch is the only branch, and it was the one that
gave up.

Stopping IS correct in one case: when every candidate is pinned by a RAM copy
waiting on its own write, evicting it would strand a live value. That case is
pinned separately below, together with the requirement that it be said out
loud, because from outside it is indistinguishable from a cap being ignored.
"""

from __future__ import annotations

import pytest

from voxlogica.storage import SQLiteResultsDatabase


_GB = 1 << 30


def _store(tmp_path, max_gb: float) -> SQLiteResultsDatabase:
    return SQLiteResultsDatabase(db_path=str(tmp_path / "s.db"),
                                 max_bytes=int(max_gb * _GB))


def _row(db: SQLiteResultsDatabase, node_id: str, nbytes: int, compute_ms: float) -> None:
    """One materialized payload row, written the way the persister writes one."""
    payload = db.payload_dir / f"{node_id}.bin"
    payload.write_bytes(b"")            # the bytes are accounted, not stored
    with db._lock:
        db._connection.execute(
            "INSERT INTO results (node_id, status, format_version, vox_type, "
            "descriptor_json, payload_json, payload_file, error, metadata_json, "
            "expression_json, dependencies_json, runtime_version, created_at, "
            "updated_at, accessed_at, payload_bytes, compute_ms, gd_key, "
            "eviction_tier, eviction_reason, eviction_bytes, eviction_at) "
            "VALUES (?, 'materialized', 'voxpod/1', 'image', '{}', '{}', ?, "
            "NULL, '{}', '{}', '{}', 'test', 0, 0, 0, ?, ?, ?, NULL, NULL, 0, NULL)",
            (node_id, str(payload), nbytes, compute_ms, compute_ms / max(nbytes, 1)))
        db._payload_bytes += nbytes


@pytest.mark.unit
def test_the_budget_holds_when_every_value_is_live(tmp_path) -> None:
    """The measured failure: a live probe that says yes to everything.

    600 rows is more than four scans of 128, so a single pass cannot reach the
    low-water mark -- which is exactly the shape that ran away.
    """
    db = _store(tmp_path, 1.0)
    db.set_live_probe(lambda node_id: True)          # what --sparse-cache does
    try:
        for i in range(600):
            _row(db, f"{i:064x}", 8 * (1 << 20), float(i))   # 8 MB each = 4.8 GB
        db._enforce_budget()
        held = db._payload_bytes
        assert held <= 1.0 * _GB, (
            f"the payload tier is {held/_GB:.2f} GB against a 1 GB budget; the "
            "cap did not hold")
        assert db._stats["evictions"] > 0
    finally:
        db.close()


@pytest.mark.unit
def test_dead_values_are_still_preferred_over_live_ones(tmp_path) -> None:
    """The fix must not cost the ordering: a live value is a last resort."""
    db = _store(tmp_path, 1.0)
    dead = {f"{i:064x}" for i in range(200)}
    db.set_live_probe(lambda node_id: node_id not in dead)
    try:
        for i in range(400):
            _row(db, f"{i:064x}", 8 * (1 << 20), float(i))   # 3.2 GB total
        db._enforce_budget()
        assert db._payload_bytes <= 1.0 * _GB
        assert db._stats["evicted_dead"] > 0
        assert db._stats["evicted_dead"] >= db._stats["evicted_live"], (
            "live values were evicted in preference to dead ones, which costs a "
            "recompute for nothing")
    finally:
        db.close()


@pytest.mark.unit
def test_it_stops_and_says_so_when_every_candidate_is_pinned(tmp_path, capsys) -> None:
    """The one case where giving up is right -- and must be audible.

    A payload whose RAM copy is still waiting on this very write cannot be
    evicted without stranding the live value. When that is true of everything,
    the tier grows and the operator has to be told, or it looks precisely like
    the bug above.
    """
    db = _store(tmp_path, 1.0)
    db.set_live_probe(lambda node_id: True)
    db._spill_guard = lambda node_id: True           # every RAM copy is waiting
    try:
        for i in range(400):
            _row(db, f"{i:064x}", 8 * (1 << 20), float(i))   # 3.2 GB, 3.2x budget
        db._enforce_budget()
        assert db._payload_bytes > 1.0 * _GB, (
            "a pinned payload was evicted; its live RAM copy is now stranded")
        assert db._stats["evictions"] == 0
        err = capsys.readouterr().err
        assert "against a 1 GB budget" in err and "awaiting its write" in err, (
            f"the overrun was silent, which is indistinguishable from the cap "
            f"being ignored; stderr was: {err!r}")
    finally:
        db.close()


@pytest.mark.unit
def test_a_store_under_budget_evicts_nothing(tmp_path) -> None:
    db = _store(tmp_path, 10.0)
    db.set_live_probe(lambda node_id: True)
    try:
        for i in range(50):
            _row(db, f"{i:064x}", 8 * (1 << 20), float(i))   # 0.4 GB
        db._enforce_budget()
        assert db._stats["evictions"] == 0
        assert db._payload_bytes == 50 * 8 * (1 << 20)
    finally:
        db.close()
