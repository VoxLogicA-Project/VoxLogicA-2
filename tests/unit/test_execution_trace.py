from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.inspectable_sequence import (
    InspectableMappedSequence,
    InspectableRangeSequence,
    InspectableSequenceValue,
)
from voxlogica.execution_strategy.strict import StrictExecutionStrategy
from voxlogica.execution_strategy.dask import DaskExecutionStrategy
from voxlogica.policy import runtime_policy_scope
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program, reduce_program_with_bindings
from voxlogica.serve_support import _deserialize_runtime_goal_values, _serialize_runtime_goal_values
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
def test_strict_strategy_reports_runtime_cache_hits(tmp_path: Path) -> None:
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
    assert second.cache_summary["cached_store"] == 0
    assert second.cache_summary["cached_local"] > 0
    assert isinstance(second.node_events, list)
    assert any(event.get("status") == "cached" for event in second.node_events)


@pytest.mark.unit
def test_map_primitive_callable_persists_without_reparse_errors(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let mapped = map(range, range(2,5))
        """
    )
    workplan, bindings = reduce_program_with_bindings(source)
    mapped_id = bindings["mapped"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = StrictExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        result = strategy.run(prepared, goals=[mapped_id])
        assert result.success is True

        assert prepared.materialization_store.flush(timeout_s=5.0) is True
        meta = prepared.materialization_store.metadata(mapped_id)
        assert meta.get("persisted") is True
        assert "persist_error" not in meta

        value = prepared.materialization_store.get(mapped_id)
        rows = [list(item.iter_values()) for item in value.iter_values()]
        assert rows == [[0, 1], [0, 1, 2], [0, 1, 2, 3]]
    finally:
        db.close()


@pytest.mark.unit
def test_strict_strategy_range_goal_is_inspectable(tmp_path: Path) -> None:
    source = parse_program_content('let xs = range(2,5)')
    workplan, bindings = reduce_program_with_bindings(source)
    xs_id = bindings["xs"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = StrictExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        result = strategy.run(prepared, goals=[xs_id])
        assert result.success is True

        value = prepared.materialization_store.get(xs_id)
        assert isinstance(value, InspectableRangeSequence)
        assert value.page(offset=0, limit=3) == [2, 3, 4]
        assert value.child_ref(1).child_id != value.child_ref(2).child_id
        overlapping = InspectableRangeSequence(parent_ref="other-range", start=3, stop=6)
        assert value.child_ref(1).child_id == overlapping.child_ref(0).child_id
    finally:
        db.close()


@pytest.mark.unit
def test_strict_strategy_map_produces_nested_inspectable_sequences(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let mapped = map(range, range(2,4))
        """
    )
    workplan, bindings = reduce_program_with_bindings(source)
    mapped_id = bindings["mapped"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = StrictExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        result = strategy.run(prepared, goals=[mapped_id])
        assert result.success is True

        value = prepared.materialization_store.get(mapped_id)
        assert isinstance(value, InspectableMappedSequence)

        first = value.resolve_item(0)
        second = value.resolve_item(1)
        assert isinstance(first, InspectableSequenceValue)
        assert isinstance(second, InspectableSequenceValue)
        assert first.page(offset=0, limit=5) == [0, 1]
        assert second.page(offset=0, limit=5) == [0, 1, 2]
        assert first.child_ref(1).child_id == second.child_ref(1).child_id
    finally:
        db.close()


@pytest.mark.unit
def test_strict_strategy_for_expression_matches_map_semantics(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let mapped =
          for x in range(2,4) do
             range(0,x)
        """
    )
    workplan, bindings = reduce_program_with_bindings(source)
    mapped_id = bindings["mapped"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = StrictExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        result = strategy.run(prepared, goals=[mapped_id])
        assert result.success is True

        value = prepared.materialization_store.get(mapped_id)
        assert isinstance(value, InspectableMappedSequence)
        rows = [value.resolve_item(index).page(offset=0, limit=5) for index in range(2)]
        assert rows == [[0, 1], [0, 1, 2]]
    finally:
        db.close()


@pytest.mark.unit
def test_dask_strategy_runtime_closure_map_still_works(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let mapped =
          for x in range(2,4) do
             x + 1
        """
    )
    workplan, bindings = reduce_program_with_bindings(source)
    mapped_id = bindings["mapped"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = DaskExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        result = strategy.run(prepared, goals=[mapped_id])
        assert result.success is True

        value = prepared.materialization_store.get(mapped_id)
        assert list(value.iter_values()) == [3, 4]
    finally:
        db.close()


@pytest.mark.unit
def test_dask_strategy_serve_mode_range_goal_is_inspectable(tmp_path: Path) -> None:
    source = parse_program_content('let xs = range(2,5)')
    workplan, bindings = reduce_program_with_bindings(source)
    xs_id = bindings["xs"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = DaskExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        with runtime_policy_scope(serve_mode=True):
            result = strategy.run(prepared, goals=[xs_id])
        assert result.success is True

        value = prepared.materialization_store.get(xs_id)
        assert isinstance(value, InspectableRangeSequence)
        assert value.page(offset=0, limit=3) == [2, 3, 4]
    finally:
        db.close()


@pytest.mark.unit
def test_dask_strategy_serve_mode_primitive_map_stays_inspectable(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let mapped = map(range, range(2,4))
        """
    )
    workplan, bindings = reduce_program_with_bindings(source)
    mapped_id = bindings["mapped"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = DaskExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        with runtime_policy_scope(serve_mode=True):
            result = strategy.run(prepared, goals=[mapped_id])
        assert result.success is True

        value = prepared.materialization_store.get(mapped_id)
        assert isinstance(value, InspectableMappedSequence)

        first = value.resolve_item(0)
        second = value.resolve_item(1)
        assert isinstance(first, InspectableSequenceValue)
        assert isinstance(second, InspectableSequenceValue)
        assert first.page(offset=0, limit=5) == [0, 1]
        assert second.page(offset=0, limit=5) == [0, 1, 2]
    finally:
        db.close()


@pytest.mark.unit
def test_dask_strategy_serve_mode_runtime_closure_map_survives_worker_serialization(tmp_path: Path) -> None:
    source = parse_program_content(
        """
        let mapped =
          for x in range(2,4) do
             range(0,x)
        """
    )
    workplan, bindings = reduce_program_with_bindings(source)
    mapped_id = bindings["mapped"]
    plan = workplan.to_symbolic_plan()

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    try:
        strategy = DaskExecutionStrategy(results_database=db)
        prepared = strategy.compile(plan)
        with runtime_policy_scope(serve_mode=True):
            result = strategy.run(prepared, goals=[mapped_id])
        assert result.success is True

        value = prepared.materialization_store.get(mapped_id)
        assert isinstance(value, InspectableMappedSequence)

        restored_map = _deserialize_runtime_goal_values(
            _serialize_runtime_goal_values({mapped_id: value})
        )
        restored = restored_map[mapped_id]
        first = restored.resolve_item(0)
        second = restored.resolve_item(1)
        assert isinstance(first, InspectableSequenceValue)
        assert isinstance(second, InspectableSequenceValue)
        assert first.page(offset=0, limit=5) == [0, 1]
        assert second.page(offset=0, limit=5) == [0, 1, 2]
    finally:
        db.close()
