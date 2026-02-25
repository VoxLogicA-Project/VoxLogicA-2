from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxlogica.serve_support import (
    PlaygroundJob,
    PlaygroundJobManager,
    build_storage_stats_snapshot,
    build_test_dashboard_snapshot,
    inspect_store_result,
    list_playground_programs,
    list_store_results_snapshot,
    load_playground_program,
    parse_playground_examples,
    render_store_result_nifti_gz,
    render_store_result_png,
)
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_parse_playground_examples_extracts_comment_directives():
    markdown = """
    <!-- vox:playground
    id: hello
    title: Hello Example
    module: default
    level: intro
    strategy: strict
    description: tiny sample
    -->
    ```imgql
    print "hello" 1 + 2
    ```
    """
    examples = parse_playground_examples(markdown)
    assert len(examples) == 1
    example = examples[0]
    assert example["id"] == "hello"
    assert example["title"] == "Hello Example"
    assert example["module"] == "default"
    assert example["strategy"] == "strict"
    assert 'print "hello" 1 + 2' in example["code"]


@pytest.mark.unit
def test_build_test_dashboard_snapshot_reads_junit_coverage_and_perf(tmp_path: Path):
    reports = tmp_path / "reports"
    perf = reports / "perf"
    perf.mkdir(parents=True)

    (reports / "junit.xml").write_text(
        """
        <testsuites>
          <testsuite tests="3" failures="1" errors="0" skipped="1" time="0.9">
            <testcase classname="a" name="ok" time="0.1" />
            <testcase classname="a" name="bad" time="0.2"><failure message="boom" /></testcase>
            <testcase classname="a" name="skip" time="0.0"><skipped /></testcase>
          </testsuite>
        </testsuites>
        """,
        encoding="utf-8",
    )
    (reports / "coverage.xml").write_text(
        """
        <coverage line-rate="0.8" branch-rate="0.5" lines-covered="8" lines-valid="10"
                  branches-covered="5" branches-valid="10">
          <packages>
            <package name="vox.a" line-rate="0.8" />
            <package name="vox.b" line-rate="0.6" />
          </packages>
        </coverage>
        """,
        encoding="utf-8",
    )
    (perf / "vox1_vs_vox2_perf.svg").write_text("<svg/>", encoding="utf-8")
    (perf / "vox1_vs_vox2_perf.json").write_text(
        json.dumps(
            {
                "vox1_median_s": 1.2,
                "vox2_median_s": 0.8,
                "speed_ratio": 1.5,
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_test_dashboard_snapshot(reports_dir=reports)
    assert snapshot["available"] is True
    assert snapshot["junit"]["summary"]["total"] == 3
    assert snapshot["junit"]["summary"]["failed"] == 1
    assert snapshot["coverage"]["summary"]["line_percent"] == 80.0
    assert snapshot["performance"]["available"] is True
    assert snapshot["performance"]["speed_ratio"] == 1.5


@pytest.mark.unit
def test_build_storage_stats_snapshot_summarizes_sqlite_backend(tmp_path: Path):
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success("node-1", {"x": 1})
    db.put_failure("node-2", "boom")
    try:
        snapshot = build_storage_stats_snapshot(db)
    finally:
        db.close()

    assert snapshot["available"] is True
    assert snapshot["summary"]["total_records"] == 2
    assert snapshot["summary"]["materialized_records"] == 1
    assert snapshot["summary"]["failed_records"] == 1


class _FakeRecvConnEOF:
    def poll(self) -> bool:
        return True

    def recv(self):  # noqa: ANN001
        raise EOFError()

    def close(self) -> None:
        return


@pytest.mark.unit
def test_playground_manager_handles_eof_from_worker_pipe() -> None:
    manager = PlaygroundJobManager()
    job = PlaygroundJob(
        job_id="job-eof",
        request_payload={},
        created_at=0.0,
        status="running",
        recv_conn=_FakeRecvConnEOF(),
    )
    manager._jobs[job.job_id] = job

    payload = manager.list_jobs()
    assert payload["total_jobs"] == 1
    listed = payload["jobs"][0]
    assert listed["job_id"] == "job-eof"
    assert listed["status"] == "failed"
    assert "terminated before delivering result payload" in str(listed["error"])
    assert "traceback" not in listed


@pytest.mark.unit
def test_playground_job_public_payload_omits_traceback() -> None:
    job = PlaygroundJob(
        job_id="job-1",
        request_payload={"execution_strategy": "dask", "_job_kind": "value-resolve"},
        created_at=0.0,
        status="failed",
        error="boom",
    )
    payload = job.as_public(include_result=False, include_log_tail=False)
    assert payload["request"]["job_kind"] == "value-resolve"
    assert "traceback" not in payload


@pytest.mark.unit
def test_playground_program_library_uses_fixed_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    load_dir = tmp_path / "playground-programs"
    load_dir.mkdir(parents=True)
    (load_dir / "demo.imgql").write_text('print "hello" 1', encoding="utf-8")
    monkeypatch.setenv("VOXLOGICA_SERVE_LOAD_DIR", str(load_dir))

    listing = list_playground_programs(limit=20)
    assert listing["available"] is True
    assert listing["load_dir"] == str(load_dir.resolve())
    assert [entry["path"] for entry in listing["files"]] == ["demo.imgql"]

    loaded = load_playground_program("demo.imgql")
    assert loaded["path"] == "demo.imgql"
    assert loaded["content"].strip() == 'print "hello" 1'

    with pytest.raises(ValueError):
        load_playground_program("../outside.imgql")


@pytest.mark.unit
def test_store_results_snapshot_and_inspection_describe_types(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success(
        "node-a",
        {
            "arr2d": np.arange(9, dtype=np.float32).reshape(3, 3),
            "arr3d": np.arange(27, dtype=np.float32).reshape(3, 3, 3),
            "nums": [1, 2, 3],
            "label": "hello",
        },
    )
    try:
        snapshot = list_store_results_snapshot(db, limit=10)
        assert snapshot["available"] is True
        assert snapshot["summary"]["materialized"] == 1
        assert snapshot["records"][0]["node_id"] == "node-a"

        root = inspect_store_result(db, node_id="node-a")
        assert root["status"] == "materialized"
        assert root["descriptor"]["kind"] == "mapping"

        arr2d = inspect_store_result(db, node_id="node-a", path="/arr2d")
        assert arr2d["descriptor"]["kind"] == "ndarray"
        assert arr2d["descriptor"]["render"]["kind"] == "image2d"

        arr3d = inspect_store_result(db, node_id="node-a", path="/arr3d")
        assert arr3d["descriptor"]["render"]["kind"] == "medical-volume"

        nums = inspect_store_result(db, node_id="node-a", path="/nums")
        assert nums["descriptor"]["numeric_values"] == [1.0, 2.0, 3.0]
    finally:
        db.close()


@pytest.mark.unit
def test_store_result_renderers_emit_png_and_nifti(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("SimpleITK")
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success("node-png", np.arange(25, dtype=np.float32).reshape(5, 5))
    db.put_success("node-nii", np.arange(125, dtype=np.float32).reshape(5, 5, 5))
    try:
        png = render_store_result_png(db, node_id="node-png")
        nii = render_store_result_nifti_gz(db, node_id="node-nii")
    finally:
        db.close()

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert nii.startswith(b"\x1f\x8b")
