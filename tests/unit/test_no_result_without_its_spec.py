"""A stored value must carry the recipe that produced it.

WHY THIS EXISTS. `record_lineage` is called from `NodeTable.intern`, and the
reducer never goes through it: it is handed the engine's own node dict inside a
`WorkPlan` and writes specs into it directly. So only the nodes `adopt_plan`
interns -- the STATIC plan -- were ever explained, and every node a runtime loop
expansion produced went unrecorded.

Measured on a sixty-case sweep's store, 2026-09-13:

    results rows : 3,252,459
    node   rows :   107,728      <- exactly the static plan, not one runtime node

Three million values whose recipe existed nowhere. The values were fine, and
useless: a warm run cannot ask "is the distance transform of case 37 on disk?"
without an id, and it cannot get the id without redoing the expansion that
produced it -- hours of pure Python to rediscover what was already on disk.

The fix is not a guard that can be forgotten; the write API REQUIRES the row and
writes it in the same transaction as the value.
"""

from __future__ import annotations

import pytest

from voxlogica.storage import SQLiteResultsDatabase


def _backend(tmp_path):
    return SQLiteResultsDatabase(str(tmp_path / "s.db"))


@pytest.mark.unit
def test_a_result_without_a_spec_is_refused(tmp_path) -> None:
    """The entry is malformed, and the store says so instead of accepting it."""
    backend = _backend(tmp_path)
    node_id = "a" * 64
    with pytest.raises(ValueError, match="no spec"):
        backend.put_success_batch([(node_id, 41, {"source": "test"}, 1.0, None, None)])


@pytest.mark.unit
def test_a_result_and_its_spec_land_together(tmp_path) -> None:
    """One transaction: the value and the recipe, or neither."""
    backend = _backend(tmp_path)
    node_id = "b" * 64
    dep_id = "c" * 64
    row = (bytes.fromhex(node_id), "primitive", "vox1.not",
           bytes.fromhex(dep_id), None, None)

    backend.put_success_batch([(node_id, 42, {"source": "test"}, 1.0, None, row)])

    record = backend.get_record(node_id)
    assert record is not None, "the value is stored"
    stored = backend._reader().execute(
        "SELECT kind, operator, args FROM node WHERE hash = ?",
        (bytes.fromhex(node_id),)).fetchone()
    assert stored is not None, "and so is its recipe"
    assert stored[0] == "primitive"
    assert stored[1] == "vox1.not"
    assert stored[2] == bytes.fromhex(dep_id), "including what it was computed FROM"


@pytest.mark.unit
def test_every_stored_result_has_a_spec(tmp_path) -> None:
    """The invariant itself, stated as a query anyone can run on any store.

    This is the check that would have caught the defect on day one, and it is
    the one to run against a store before trusting a warm run against it.
    """
    backend = _backend(tmp_path)
    for i in range(8):
        nid = f"{i:064x}"
        row = (bytes.fromhex(nid), "primitive", "vox1.dt2", b"", None, None)
        backend.put_success_batch([(nid, i, {"source": "test"}, 1.0, None, row)])

    orphans = backend._reader().execute(
        "SELECT COUNT(*) FROM results r WHERE NOT EXISTS "
        "(SELECT 1 FROM node n WHERE n.hash = CAST(r.node_id AS BLOB))"
    ).fetchone()[0]
    # `node_id` is hex text and `hash` is a blob, so the join is done in Python
    # rather than trusting a CAST that silently matches nothing.
    ids = [r[0] for r in backend._reader().execute("SELECT node_id FROM results")]
    have = {r[0] for r in backend._reader().execute("SELECT hash FROM node")}
    missing = [i for i in ids if bytes.fromhex(i) not in have]
    assert missing == [], f"{len(missing)} stored results have no spec"
