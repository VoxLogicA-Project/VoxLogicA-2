"""A value that IS None must reload from the store as present, not as absent.

`simpleitk.WriteImage` returns None, and so does a closure; both are persisted.
`NodeTable.load` used to return None for "no row", so a stored None read as a
miss: the node was a loop body this run had never interned, and a warm run died
naming it. It is why `exported` and `exported_planes` -- loops of WriteImage --
were the two goals the nnU-Net sweep never delivered warm.
"""

from __future__ import annotations

import pytest

from voxlogica.engine.node_table import MISSING, NodeTable
from voxlogica.lazy.ir import NodeSpec
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_a_stored_none_is_a_value_not_a_miss(tmp_path) -> None:
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"))
    try:
        # The same road a real run takes: complete with None, flush, evict.
        table = NodeTable(backend=backend)
        table.nodes["side-effect"] = NodeSpec(kind="primitive", operator="simpleitk.WriteImage")
        table.begin("side-effect")
        table.complete("side-effect", None, compute_ms=5.0, critical=True)
        table.flush()
        table.evict("side-effect")
        assert "side-effect" not in table.values

        assert table.load("never-written") is MISSING, "no row is a miss"
        assert table.load("side-effect") is None, "a stored None is the value None"
        assert "side-effect" in table.values, "and it is resident afterwards"
    finally:
        backend.close()
