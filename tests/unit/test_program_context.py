from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import StaticAnalysisError, reduce_program_with_bindings
from voxlogica.storage import NoCacheStorageBackend


@pytest.mark.unit
def test_program_sysvars_resolve_from_source_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program_file = tmp_path / "demo-case.imgql"
    program_file.write_text(
        'export_root = concat("output/", $stem)\nprint "export_root" export_root\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    program = parse_program_content(program_file.read_text(encoding="utf-8"), source_name=str(program_file))
    workplan, bindings = reduce_program_with_bindings(program, source_name=str(program_file))

    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True)
    prepared = engine.compile_plan(workplan)
    result = engine.run_prepared(prepared)
    assert result.success, result.failed_operations

    export_root = prepared.values[bindings["export_root"].operation_id]
    assert export_root == "output/demo-case"


@pytest.mark.unit
def test_program_sysvar_cwd_is_process_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program_file = tmp_path / "cwd-demo.imgql"
    program_file.write_text('cwd_path = $cwd\nprint "cwd" cwd_path\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    program = parse_program_content(program_file.read_text(encoding="utf-8"), source_name=str(program_file))
    workplan, bindings = reduce_program_with_bindings(program, source_name=str(program_file))

    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True)
    prepared = engine.compile_plan(workplan)
    result = engine.run_prepared(prepared)
    assert result.success, result.failed_operations

    cwd = prepared.values[bindings["cwd_path"].operation_id]
    assert cwd == str(tmp_path.resolve())


@pytest.mark.unit
def test_program_sysvars_cannot_be_redeclared() -> None:
    program = parse_program_content('$stem = "override"\n', source_name="demo.imgql")
    with pytest.raises(StaticAnalysisError, match="reserved"):
        reduce_program_with_bindings(program, source_name="demo.imgql")
