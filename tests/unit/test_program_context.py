from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import StaticAnalysisError, reduce_program_with_bindings
from voxlogica.storage import NoCacheStorageBackend


def _run_and_capture(workplan, capsys: pytest.CaptureFixture[str]) -> str:
    """Execute a plan and return its captured stdout (the goal print output)."""
    result = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True).execute_workplan(workplan)
    assert result.success, result.failed_operations
    return capsys.readouterr().out


@pytest.mark.unit
def test_program_sysvars_resolve_from_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    program_file = tmp_path / "demo-case.imgql"
    program_file.write_text(
        'export_root = concat("output/", $stem)\nprint "export_root" export_root\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    program = parse_program_content(program_file.read_text(encoding="utf-8"), source_name=str(program_file))
    workplan, _bindings = reduce_program_with_bindings(program, source_name=str(program_file))

    # Assert on the observable print goal, not on internal intermediate values:
    # this holds for either execution strategy (the engine evicts intermediates).
    assert "export_root=output/demo-case" in _run_and_capture(workplan, capsys)


@pytest.mark.unit
def test_program_sysvar_cwd_is_process_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    program_file = tmp_path / "cwd-demo.imgql"
    program_file.write_text('cwd_path = $cwd\nprint "cwd" cwd_path\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    program = parse_program_content(program_file.read_text(encoding="utf-8"), source_name=str(program_file))
    workplan, _bindings = reduce_program_with_bindings(program, source_name=str(program_file))

    assert f"cwd={tmp_path.resolve()}" in _run_and_capture(workplan, capsys)


@pytest.mark.unit
def test_program_sysvars_cannot_be_redeclared() -> None:
    program = parse_program_content('$stem = "override"\n', source_name="demo.imgql")
    with pytest.raises(StaticAnalysisError, match="reserved"):
        reduce_program_with_bindings(program, source_name="demo.imgql")
