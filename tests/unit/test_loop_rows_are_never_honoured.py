"""A cached row for an expansion-produced node is a promise nobody can keep.

MEASURED, on the sixty-case oracle sweep's own store. One row:

    metadata_json  {"operator":"default.for_loop","source":"runtime"}
    status         materialized
    vox_type       sequence
    payload_file   None
    payload_json   19,404 bytes, sequence-json-v1, length 225
    row in `node`  absent
    of the 225 handle refs: in the `node` (spec) table    0 / 225
    of the 225 handle refs: materialized in `results`   203 / 225

`persisted()` answers from the materialized-id index, so a warm run reported
that node available; `_schedule_subgraph` pruned its whole subtree ("cached:
loaded on demand"); and the expansion that alone interns those 225 element
specs never ran. The first deep resolve reaching one of the 22 elements with no
value raised `NeedsExpansion` from inside `resolve_deep` on a POOL thread --
where the engine cannot register an expansion -- so it surfaced as
`NodeExecutionError: default.argmax failed`, deterministically, 37 s into every
run against that store.

`node_table._references_are_answerable` already documents the intended
behaviour: "a warm run that hits the stored container would SKIP the loop
expansion that defines its elements; refusing the hit makes it expand, which
interns them, and each element then hits the store on its own." The refusal
worked. The pruning happened one level above it, so the expansion never got the
chance.

Both halves are pinned here, because either alone leaves the bug:

  * WRITE: such a row must not be created;
  * READ: every store already written contains them, and they must be ignored
    rather than trusted.

`default.sequence` must keep working in both halves -- its args ARE its element
ids, so its spec is self-sufficient and it is the thing actually worth caching.
"""

from __future__ import annotations

import json

import pytest

from voxlogica.engine.core import _EXPANDED_OPERATORS, _SEQUENCE_OPERATORS
from voxlogica.storage import SQLiteResultsDatabase


def _row(db: SQLiteResultsDatabase, node_id: str, operator: str) -> None:
    """Write one materialized row the way the persister does, by hand.

    Deliberately not through the engine: the point is a store that ALREADY
    contains such a row, written by a build that did not know better, which is
    the situation on every existing store.
    """
    payload = json.dumps({"encoding": "sequence-json-v1", "length": 2,
                          "value": [{"__vox_handle__": "a" * 64},
                                    {"__vox_handle__": "b" * 64}]})
    with db._lock:
        db._connection.execute(
            "INSERT INTO results (node_id, status, format_version, vox_type, "
            "descriptor_json, payload_json, payload_file, error, metadata_json, "
            "expression_json, dependencies_json, runtime_version, created_at, "
            "updated_at, accessed_at, payload_bytes, compute_ms, gd_key, "
            "eviction_tier, eviction_reason, eviction_bytes, eviction_at) "
            "VALUES (?, 'materialized', 'voxpod/1', 'sequence', '{}', ?, NULL, "
            "NULL, ?, '{}', '{}', 'test', 0, 0, 0, 0, 0.0, 0.0, NULL, NULL, 0, NULL)",
            (node_id, payload,
             json.dumps({"operator": operator, "source": "runtime"})))


@pytest.mark.unit
def test_the_expanded_operators_are_the_rewritten_ones_only() -> None:
    """`default.sequence` must not be in the refusal set: it is cacheable."""
    assert _EXPANDED_OPERATORS <= _SEQUENCE_OPERATORS
    for name in ("for_loop", "default.for_loop", "map", "default.map",
                 "filter", "default.filter"):
        assert name in _EXPANDED_OPERATORS, name
    for name in ("default.sequence", "sequence"):
        assert name not in _EXPANDED_OPERATORS, (
            f"{name} rebuilds from its own args and is what caching is FOR")


@pytest.mark.unit
def test_a_stored_loop_row_is_not_reported_as_available(tmp_path, capsys) -> None:
    """The read half: an existing poisoned store must go back to merely cold."""
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "poisoned.db"))
    loop_id = "b2" + "8" * 62
    seq_id = "c3" + "9" * 62
    _row(db, loop_id, "default.for_loop")
    _row(db, seq_id, "default.sequence")

    ids = set(db.materialized_ids())
    assert loop_id not in ids, (
        "the for_loop row was reported available; a warm run will prune its "
        "subtree and the expansion that interns its elements will never run")
    assert seq_id in ids, "a plain sequence row is honourable and must be kept"
    assert "ignoring 1 cached row" in capsys.readouterr().err


@pytest.mark.unit
def test_the_id_index_the_engine_builds_agrees(tmp_path) -> None:
    """`NodeTable.persisted` is the predicate that prunes; check it directly."""
    from voxlogica.engine.node_table import NodeTable

    db = SQLiteResultsDatabase(db_path=str(tmp_path / "p2.db"))
    loop_id = "d4" + "7" * 62
    seq_id = "e5" + "6" * 62
    _row(db, loop_id, "for_loop")
    _row(db, seq_id, "sequence")

    table = NodeTable(backend=db)
    assert not table.persisted(loop_id)
    assert table.persisted(seq_id)


@pytest.mark.unit
def test_a_malformed_metadata_row_is_kept_rather_than_dropped(tmp_path) -> None:
    """Unreadable metadata must not silently delete a good cache entry.

    The filter exists to refuse a specific, identified shape. Anything it
    cannot parse is not that shape as far as it can tell, and dropping it would
    trade a correctness bug for a performance one.
    """
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "p3.db"))
    ok_id = "f6" + "5" * 62
    _row(db, ok_id, "vox1.dt")
    with db._lock:
        db._connection.execute(
            "UPDATE results SET metadata_json = ? WHERE node_id = ?",
            ("{not json at all, mentions for_loop", ok_id))
    assert ok_id in set(db.materialized_ids())


@pytest.mark.unit
def test_the_substring_prefilter_cannot_produce_a_false_refusal(tmp_path) -> None:
    """An operator merely NAMED like one of these is not one of these."""
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "p4.db"))
    keep = "a7" + "4" * 62
    _row(db, keep, "vox1.mapping_of_filter_for_loops")
    assert keep in set(db.materialized_ids())
