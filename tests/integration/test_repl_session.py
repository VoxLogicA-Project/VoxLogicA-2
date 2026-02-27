from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.repl import ReplProgramResult, ReplSession, ReplValue
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.integration
def test_repl_evaluates_expression_and_persists_result(tmp_path: Path):
    db_path = tmp_path / "repl_results.db"
    storage = SQLiteResultsDatabase(db_path=db_path)
    session = ReplSession(strategy="dask", storage=storage)

    try:
        definition_result = session.execute_input("let inc(x)=x+1")
        assert isinstance(definition_result, ReplProgramResult)
        assert definition_result.declarations_added == 1

        value_result = session.execute_input("inc(41)")
        assert isinstance(value_result, ReplValue)
        assert value_result.value == 42.0
        assert value_result.persisted

        record = storage.get_record(value_result.node_id)
        assert record is not None
        assert record.value == 42.0
    finally:
        storage.close()


@pytest.mark.integration
def test_repl_load_file_adds_declarations_and_skips_goals(tmp_path: Path):
    db_path = tmp_path / "repl_results.db"
    storage = SQLiteResultsDatabase(db_path=db_path)
    session = ReplSession(strategy="dask", storage=storage)

    source = tmp_path / "defs.imgql"
    source.write_text(
        'let inc(x)=x+1\nprint "unused" inc(1)\n',
        encoding="utf-8",
    )

    try:
        load_result = session.load_file(source, execute_goals=False)
        assert load_result.declarations_added == 1
        assert load_result.goals_skipped == 1

        value_result = session.execute_input("inc(2)")
        assert isinstance(value_result, ReplValue)
        assert value_result.value == 3.0
    finally:
        storage.close()
