"""Adding a column must not cost the user a day of compute.

The store used to initialize by comparing its column set and, on any
difference, DROPPING the table and deleting every payload file. That made each
new column a choice between improving the store and destroying every existing
one -- and these caches are hours (this project's is 5.6 GB) of compute.

SQLite adds a column in O(1), and every column this store adds carries a
default, so an existing row is valid the instant it appears. Growing in place
is therefore the correct behaviour, and dropping is reserved for a table that
genuinely is not this one.
"""

from __future__ import annotations

import sqlite3

import pytest

from voxlogica.storage import (
    MATERIALIZED_STATUS,
    STORE_SCHEMA_VERSION,
    SQLiteResultsDatabase,
    _ADDABLE_COLUMNS,
    _RESULTS_TABLE_COLUMNS,
)


OLD_COLUMNS = sorted(_RESULTS_TABLE_COLUMNS - set(_ADDABLE_COLUMNS))


def make_old_store(path, *, columns=None, version: int = 4):
    """A store as an earlier VoxLogicA left it: no eviction columns."""
    columns = OLD_COLUMNS if columns is None else columns
    connection = sqlite3.connect(path)
    ddl = ", ".join(f"{name} TEXT" if name != "node_id" else "node_id TEXT PRIMARY KEY"
                    for name in columns)
    connection.execute(f"CREATE TABLE results ({ddl})")
    connection.execute(
        f"INSERT INTO results ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        ["old_node" if c == "node_id" else
         MATERIALIZED_STATUS if c == "status" else
         "{}" if c.endswith("_json") else "0" for c in columns],
    )
    connection.execute(f"PRAGMA user_version = {version}")
    connection.commit()
    connection.close()


def test_an_older_store_is_grown_in_place_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    make_old_store(path)
    payloads = tmp_path / "old.db.files"
    payloads.mkdir()
    (payloads / "keepme.bin").write_bytes(b"a payload from before the migration")

    store = SQLiteResultsDatabase(path)

    assert store._results_columns() == _RESULTS_TABLE_COLUMNS
    assert store.has("old_node") is True                       # the row survived
    assert (payloads / "keepme.bin").exists()                  # so did its payload
    version = store._connection.execute("PRAGMA user_version").fetchone()[0]
    assert int(version) == STORE_SCHEMA_VERSION


def test_the_added_columns_are_usable_immediately(tmp_path):
    path = tmp_path / "old.db"
    make_old_store(path)
    store = SQLiteResultsDatabase(path)

    row = store._connection.execute(
        "SELECT payload_file, payload_bytes, gd_key FROM results WHERE node_id = ?",
        ("old_node",)).fetchone()
    store._evict_row("old_node", row[0], 0, 0.0, "evicted_dead", "migrated then evicted")

    tier, reason = store._connection.execute(
        "SELECT eviction_tier, eviction_reason FROM results WHERE node_id = ?",
        ("old_node",)).fetchone()
    assert (tier, reason) == ("evicted_dead", "migrated then evicted")


def test_a_table_that_is_not_ours_is_still_replaced(tmp_path):
    # An extra, unknown column means this is not an older version of this
    # table: it is something else, and keeping it would be a guess.
    path = tmp_path / "foreign.db"
    make_old_store(path, columns=OLD_COLUMNS + ["something_we_never_wrote"])

    store = SQLiteResultsDatabase(path)

    assert store._results_columns() == _RESULTS_TABLE_COLUMNS
    assert store.has("old_node") is False                      # dropped, as it must be


def test_a_fresh_store_has_every_column(tmp_path):
    store = SQLiteResultsDatabase(tmp_path / "fresh.db")

    assert store._results_columns() == _RESULTS_TABLE_COLUMNS


def test_reopening_a_current_store_changes_nothing(tmp_path):
    path = tmp_path / "store.db"
    first = SQLiteResultsDatabase(path)
    first.put_success("n1", b"x" * 128, metadata={}, compute_ms=1.0)
    del first

    second = SQLiteResultsDatabase(path)

    assert second.has("n1") is True


def test_every_addable_column_declares_a_default(tmp_path):
    # A column without a default cannot be added to a table that already has
    # rows without rewriting it -- which is the thing this whole path avoids.
    for name, ddl in _ADDABLE_COLUMNS.items():
        assert "NOT NULL" not in ddl or "DEFAULT" in ddl, name
