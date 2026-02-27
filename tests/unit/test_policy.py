from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.features import handle_run
from voxlogica.parser import parse_program_content
from voxlogica.policy import validate_workplan_policy
from voxlogica.reducer import reduce_program, reduce_program_with_bindings


def _reduce(program_text: str):
    return reduce_program(parse_program_content(program_text))


@pytest.mark.unit
def test_non_legacy_blocks_simpleitk_writeimage() -> None:
    workplan = _reduce(
        '\n'.join(
            [
                'import "simpleitk"',
                'let out = WriteImage(0, "tests/output/blocked.nii.gz")',
            ]
        )
    )

    diagnostics = validate_workplan_policy(workplan, legacy=False, serve_mode=False)
    assert any(diag.code == "E_EFFECT_BLOCKED" for diag in diagnostics)
    assert any(str(diag.symbol or "").endswith("WriteImage") for diag in diagnostics)


@pytest.mark.unit
def test_non_legacy_blocks_nnunet_effects() -> None:
    workplan = _reduce(
        '\n'.join(
            [
                'import "nnunet"',
                'let out = nnunet.train("images", "labels", "mods", "work")',
            ]
        )
    )

    diagnostics = validate_workplan_policy(workplan, legacy=False, serve_mode=False)
    assert any(diag.code == "E_EFFECT_BLOCKED" for diag in diagnostics)
    assert any(str(diag.symbol or "").startswith("nnunet.") for diag in diagnostics)


@pytest.mark.unit
def test_legacy_allows_effectful_primitives() -> None:
    workplan = _reduce(
        '\n'.join(
            [
                'import "simpleitk"',
                'let out = WriteImage(0, "tests/output/allowed.nii.gz")',
            ]
        )
    )

    diagnostics = validate_workplan_policy(workplan, legacy=True, serve_mode=False)
    assert not any(diag.code == "E_EFFECT_BLOCKED" for diag in diagnostics)


@pytest.mark.unit
def test_serve_read_root_policy_allows_inside_and_blocks_outside(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True)
    inside = allowed_root / "inside.nii.gz"
    outside = tmp_path / "outside.nii.gz"

    monkeypatch.setenv("VOXLOGICA_SERVE_DATA_DIR", str(allowed_root))
    monkeypatch.delenv("VOXLOGICA_SERVE_EXTRA_READ_ROOTS", raising=False)

    allowed_plan = _reduce(
        '\n'.join(
            [
                'import "simpleitk"',
                f'let img = ReadImage("{inside}")',
            ]
        )
    )
    allowed_diags = validate_workplan_policy(allowed_plan, legacy=False, serve_mode=True)
    assert not any(diag.code == "E_READ_ROOT_POLICY" for diag in allowed_diags)

    blocked_plan = _reduce(
        '\n'.join(
            [
                'import "simpleitk"',
                f'let img = ReadImage("{outside}")',
            ]
        )
    )
    blocked_diags = validate_workplan_policy(blocked_plan, legacy=False, serve_mode=True)
    assert any(diag.code == "E_READ_ROOT_POLICY" for diag in blocked_diags)


@pytest.mark.unit
def test_handle_run_legacy_flag_controls_effect_policy() -> None:
    program = '\n'.join(
        [
            'import "simpleitk"',
            'let out = WriteImage(0, "tests/output/legacy_gate.nii.gz")',
        ]
    )

    blocked = handle_run(
        program=program,
        execute=False,
        legacy=False,
        serve_mode=False,
    )
    assert blocked.success is False
    assert blocked.error and "blocked in non-legacy mode" in blocked.error

    allowed = handle_run(
        program=program,
        execute=False,
        legacy=True,
        serve_mode=False,
    )
    assert allowed.success is True


@pytest.mark.unit
def test_handle_run_runtime_read_policy_blocks_dynamic_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True)

    monkeypatch.setenv("VOXLOGICA_SERVE_DATA_DIR", str(allowed_root))
    monkeypatch.delenv("VOXLOGICA_SERVE_EXTRA_READ_ROOTS", raising=False)

    program = '\n'.join(
        [
            'import "strings"',
            f'let p = concat("{tmp_path}", "/outside.txt")',
            'print "payload" load(p)',
        ]
    )

    result = handle_run(
        program=program,
        execute=True,
        execution_strategy="dask",
        legacy=False,
        serve_mode=True,
    )
    assert result.success is False
    assert result.error and "Execution failed with" in result.error
    execution = ((result.data or {}).get("execution") if isinstance(result.data, dict) else {}) or {}
    errors = execution.get("errors") if isinstance(execution, dict) else {}
    assert any("Serve read policy blocked" in str(message) for message in dict(errors or {}).values())


@pytest.mark.unit
def test_handle_run_goal_scoped_policy_allows_pure_target_with_unrelated_effect() -> None:
    program = '\n'.join(
        [
            'import "simpleitk"',
            "let pure = 2 + 3",
            'let side = WriteImage(0, "tests/output/unreached.nii.gz")',
            'print "pure" pure',
        ]
    )
    _workplan, bindings = reduce_program_with_bindings(parse_program_content(program))
    pure_node = bindings["pure"]

    result = handle_run(
        program=program,
        execute=True,
        execution_strategy="dask",
        _goals=[pure_node],
        legacy=False,
        serve_mode=False,
    )
    assert result.success is True


@pytest.mark.unit
def test_handle_run_emits_runtime_descriptor_for_requested_non_goal_node() -> None:
    program = '\n'.join(
        [
            "let a = 2 + 3",
            'print "done" 1',
        ]
    )
    _workplan, bindings = reduce_program_with_bindings(parse_program_content(program))
    target = bindings["a"]

    result = handle_run(
        program=program,
        execute=True,
        execution_strategy="dask",
        _goals=[target],
        _include_goal_descriptors=True,
        legacy=False,
        serve_mode=False,
    )
    assert result.success is True
    goal_results = (result.data or {}).get("goal_results", []) if isinstance(result.data, dict) else []
    target_entry = next((row for row in goal_results if str(row.get("node_id")) == str(target)), None)
    assert target_entry is not None
    assert target_entry.get("operation") == "inspect"
    assert isinstance(target_entry.get("runtime_descriptor"), dict)
