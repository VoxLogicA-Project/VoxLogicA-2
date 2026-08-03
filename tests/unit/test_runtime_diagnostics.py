"""Regression gates for release-facing runtime diagnostics."""

from __future__ import annotations

import re

from voxlogica.diagnostics.classify import build_report
from voxlogica.main import main


def _missing_image_program(tmp_path):
    program = tmp_path / "missing.imgql"
    program.write_text(
        'import "simpleitk"\nprint "image" ReadImage("/does/not/exist.nii.gz")\n',
        encoding="utf-8",
    )
    return program


def test_missing_image_is_compact_and_inspectable(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOXLOGICA_DIAGNOSTIC_DIR", str(tmp_path / "diagnostics"))
    assert main(["run", "--no-cache", str(_missing_image_program(tmp_path))]) == 1
    captured = capsys.readouterr()

    assert "error[E_IMAGE_NOT_FOUND]: Cannot open image" in captured.err
    assert "missing.imgql:2:15" in captured.err
    assert "Check the filename and dataset root." in captured.err
    assert "Traceback" not in captured.err
    details_id = re.search(r"VLX-[A-F0-9]+", captured.err).group(0)

    assert main(["errors", "show", details_id]) == 0
    details = capsys.readouterr().out
    assert "PrimitiveExecutionError" in details
    assert "SimpleITK ImageFileReader" in details


def test_error_details_opt_in_prints_traceback(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOXLOGICA_DIAGNOSTIC_DIR", str(tmp_path / "diagnostics"))
    assert main(["run", "--no-cache", "--error-details", str(_missing_image_program(tmp_path))]) == 1
    assert "Traceback" in capsys.readouterr().err


def test_failed_goal_reports_its_root_cause_not_a_stalled_progress_bar(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOXLOGICA_DIAGNOSTIC_DIR", str(tmp_path / "diagnostics"))
    program = tmp_path / "missing-directory.imgql"
    program.write_text(
        'print "paths" dir("/does/not/exist", "*.nii.gz", true, true)\n',
        encoding="utf-8",
    )

    assert main(["run", "--no-cache", str(program)]) == 1
    stderr = capsys.readouterr().err
    assert "dir root not found: /does/not/exist" in stderr
    assert "unresolved goal" not in stderr


def test_common_operation_failures_receive_stable_codes() -> None:
    assert build_report(ValueError("avg failed: mask selects no voxels")).diagnostic.code == "E_EMPTY_SELECTION"
    assert build_report(ValueError("images must have the same number of voxels")).diagnostic.code == "E_IMAGE_MISMATCH"
    assert build_report(IndexError("list index out of range")).diagnostic.code == "E_INDEX_OUT_OF_RANGE"
