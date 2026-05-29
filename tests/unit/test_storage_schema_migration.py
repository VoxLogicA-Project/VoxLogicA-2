from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from voxlogica.storage import STORE_SCHEMA_VERSION, SQLiteResultsDatabase


def _create_legacy_v1_db(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE results (
                node_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                format_version TEXT NOT NULL,
                vox_type TEXT,
                descriptor_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_bin BLOB,
                error TEXT,
                metadata_json TEXT NOT NULL,
                runtime_version TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION - 1}")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.unit
def test_legacy_results_db_is_migrated_on_open(tmp_path: Path) -> None:
    db_path = tmp_path / "results.db"
    _create_legacy_v1_db(db_path)

    db = SQLiteResultsDatabase(db_path=db_path)
    try:
        connection = sqlite3.connect(db_path)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            columns = {row[1] for row in connection.execute("PRAGMA table_info(results)").fetchall()}
        finally:
            connection.close()

        assert version == STORE_SCHEMA_VERSION
        assert "payload_file" in columns
        assert "payload_bin" not in columns
        assert "expression_json" in columns
        assert "dependencies_json" in columns
    finally:
        db.close()
