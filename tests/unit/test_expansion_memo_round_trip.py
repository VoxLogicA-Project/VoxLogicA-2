"""A stored expansion is trusted only because it is verified.

The memo says "this loop expands into that spliced sequence", and a reader that
finds it PRUNES the loop and never expands it. So a memo whose referents are
missing, or whose specs are not genuine, poisons the store for every later run
— which is not hypothetical: on 2026-09-10 a `for_loop` row held 225 handles of
which 0 had a spec, and every warm run against that store died 37 seconds in,
deterministically.

Two rules make it safe, and these are their tests:

1. a spec read back must RE-HASH to the id it was stored under, or it is not
   that spec and the caller expands instead;
2. the memo is keyed by the reducer version, so a changed expander invalidates
   memos rather than silently reusing specs it would no longer produce.
"""

from __future__ import annotations

import pytest

from voxlogica.lazy.hash import hash_node
from voxlogica.lazy.ir import NodeSpec
from voxlogica.storage import SQLiteResultsDatabase, spec_from_row, spec_row_for


@pytest.fixture
def store(tmp_path):
    return SQLiteResultsDatabase(str(tmp_path / "s.db"))


_SPEC = NodeSpec(kind="primitive", operator="vox1.dt2", args=("a" * 64,),
                 output_kind="overlay")


@pytest.mark.unit
def test_a_spec_survives_the_store_and_verifies(store) -> None:
    node_id = hash_node(_SPEC)
    store.put_lineage_batch([spec_row_for(node_id, _SPEC)])
    restored = spec_from_row(node_id, store.get_definition(node_id))
    assert restored is not None
    assert hash_node(restored) == node_id
    assert restored.operator == "vox1.dt2"
    assert restored.output_kind == "overlay"


@pytest.mark.unit
def test_a_tampered_spec_is_refused(store) -> None:
    """The check that makes a shared or copied store safe to read."""
    node_id = hash_node(_SPEC)
    row = spec_row_for(node_id, _SPEC)
    tampered = (row[0], row[1], "vox1.SOMETHING_ELSE") + row[3:]
    store.put_lineage_batch([tampered])
    assert spec_from_row(node_id, store.get_definition(node_id)) is None, (
        "a row that does not hash to its own id must never be interned")


@pytest.mark.unit
def test_a_missing_spec_is_not_an_error(store) -> None:
    assert spec_from_row("f" * 64, store.get_definition("f" * 64)) is None


@pytest.mark.unit
def test_the_memo_round_trips(store) -> None:
    store.put_expansion("1" * 64, "2" * 64, "v1")
    assert store.get_expansion("1" * 64, "v1") == "2" * 64


@pytest.mark.unit
def test_a_memo_from_another_reducer_is_not_used(store) -> None:
    """A changed expander produces different specs from the same loop, so its
    memos must not be reused. Invalidate, never silently trust."""
    store.put_expansion("1" * 64, "2" * 64, "v1")
    assert store.get_expansion("1" * 64, "v2") is None
