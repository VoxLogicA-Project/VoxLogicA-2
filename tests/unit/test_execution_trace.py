from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.execution_strategy.strict import StrictExecutionStrategy
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program, reduce_program_with_bindings
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_reduce_program_with_bindings_tracks_let_nodes() -> None:
    program = parse_program_content(
        """
        let a = 1 + 2
        let b = a + 4
        print "sum" b
        """
    )
    workplan, bindings = reduce_program_with_bindings(program)
    assert isinstance(bindings, dict)
    assert "a" in bindings
    assert "b" in bindings
    assert bindings["a"] in workplan.nodes
    assert bindings["b"] in workplan.nodes


@pytest.mark.unit
def test_strict_strategy_reports_store_cache_hits(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let a = 1 + 2
        let b = a + 4
        print "sum" b
        """
    )
    plan = reduce_program(source).to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = StrictExecutionStrategy(results_database=db)
        first = strategy.run(strategy.compile(plan))
        second = strategy.run(strategy.compile(plan))
    finally:
        db.close()

    assert first.success is True
    assert second.success is True
    assert first.cache_summary["computed"] > 0
    assert second.cache_summary["cached_store"] > 0
    assert isinstance(second.node_events, list)
    assert any(event.get("status") == "cached" for event in second.node_events)
