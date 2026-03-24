from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.inspectable_sequence import (
    BlockedComputation,
    InspectableListSequence,
    InspectableRangeSequence,
    InspectableSequenceValue,
    ItemSnapshot,
)
from voxlogica.lazy.hash import hash_sequence_item
from voxlogica.serve_support import (
    LiveRuntimeValueInspector,
    PlaygroundJob,
    PlaygroundJobManager,
    RuntimeValueInspector,
    build_storage_stats_snapshot,
    build_test_dashboard_snapshot,
    describe_runtime_value,
    freeze_runtime_value,
    get_lightweight_storage_stats_snapshot,
    inspect_runtime_value_page,
    inspect_store_result,
    inspect_store_result_page,
    list_playground_programs,
    list_store_results_snapshot,
    load_playground_program,
    parse_playground_examples,
    render_store_result_nifti,
    render_store_result_nifti_gz,
    render_store_result_png,
)
from voxlogica.storage import MaterializationStore, SQLiteResultsDatabase
from voxlogica.value_model import OverlayValue


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
    answer = 1 + 2
    ```
    """
    examples = parse_playground_examples(markdown)
    assert len(examples) == 1
    example = examples[0]
    assert example["id"] == "hello"
    assert example["title"] == "Hello Example"
    assert example["module"] == "default"
    assert example["strategy"] == "dask"
    assert "answer = 1 + 2" in example["code"]


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


@pytest.mark.unit
def test_describe_runtime_value_builds_overlay_render_urls() -> None:
    np = pytest.importorskip("numpy")
    overlay = OverlayValue.from_layers(
        [
            np.zeros((4, 4, 4), dtype=np.float32),
            np.ones((4, 4, 4), dtype=np.float32),
        ]
    )
    payload = describe_runtime_value(node_id="node-overlay", value=overlay)
    descriptor = payload["descriptor"]
    assert descriptor["vox_type"] == "overlay"
    assert descriptor["render"]["kind"] == "medical-overlay"
    layers = descriptor["render"]["layers"]
    assert len(layers) == 2
    assert layers[0]["path"] == "/0"
    assert layers[0]["label"] == "Base"
    assert layers[0]["colormap"] == "gray"
    assert layers[0]["nifti_url"].endswith("/api/v1/results/store/node-overlay/render/nii?path=%2F0")


@pytest.mark.unit
def test_get_lightweight_storage_stats_snapshot_is_async_and_cached(tmp_path: Path):
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success("node-1", {"x": 1})
    try:
        first = get_lightweight_storage_stats_snapshot(db, force_refresh=True)
        assert first["mode"] == "lightweight-async"
        assert first["supports_detailed_stats"] is False
        assert first["refreshing"] is True

        deadline = time.time() + 2.0
        latest = first
        while time.time() < deadline:
            latest = get_lightweight_storage_stats_snapshot(db)
            if latest.get("available"):
                break
            time.sleep(0.02)
        assert latest["available"] is True
        assert latest["refreshing"] is False
        assert latest["db_path"] == str(tmp_path / "results.db")
        assert "disk" in latest
        assert latest["payload_buckets"] == []
    finally:
        db.close()


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
def test_playground_manager_value_resolve_uses_inprocess_future(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voxlogica.serve_support as serve_support

    monkeypatch.setattr(serve_support, "PLAYGROUND_JOB_LOG_DIR", tmp_path)

    calls: list[dict[str, object]] = []

    def _fake_execute(
        request_payload: dict[str, object],
        log_path_str: str,
        live_inspector: object | None = None,
    ) -> dict[str, object]:
        started = time.time()
        del live_inspector
        calls.append(
            {
                "request_payload": dict(request_payload),
                "log_path": log_path_str,
            }
        )
        return {
            "ok": True,
            "result": {"execution": {"success": True, "cache_summary": {"computed": 1}}},
            "_runtime_goal_values": {"node-1": [10, 11, 12]},
            "metrics": {"wall_time_s": 0.01, "cpu_time_s": 0.01},
            "started_at": started,
            "finished_at": started + 0.01,
        }

    monkeypatch.setattr(serve_support, "_execute_playground_request", _fake_execute)

    manager = PlaygroundJobManager()
    request_payload = {
        "program": "x = 1",
        "execute": True,
        "execution_strategy": "dask",
        "_job_kind": "value-resolve",
        "_priority_node": "node-1",
        "_program_hash": "prog-1",
    }

    created = manager.ensure_value_job(
        request_payload,
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
    )
    created_job_id = str(created["job_id"])
    created_job = manager._jobs[created_job_id]
    assert created_job.process is None
    assert created_job.recv_conn is None
    assert created_job.status in {"queued", "running", "completed"}

    final = manager.get_value_job(
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
    )
    assert final is not None
    assert final["status"] == "completed"
    assert calls and str(calls[0]["log_path"]).endswith(f"{created_job_id}.log")
    preview = manager.inspect_value_job_runtime(
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
        path="/1",
    )
    assert preview is not None
    assert preview["value"] == 11
    assert preview["descriptor"]["vox_type"] == "integer"


@pytest.mark.unit
def test_playground_manager_runtime_inspection_returns_error_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voxlogica.serve_support as serve_support

    monkeypatch.setattr(serve_support, "PLAYGROUND_JOB_LOG_DIR", tmp_path)

    def _fake_execute(
        request_payload: dict[str, object],
        log_path_str: str,
        live_inspector: object | None = None,
    ) -> dict[str, object]:
        started = time.time()
        del request_payload, log_path_str, live_inspector
        return {
            "ok": True,
            "result": {"execution": {"success": True}},
            "_runtime_goal_values": {"node-1": [10, 11, 12]},
            "metrics": {"wall_time_s": 0.01, "cpu_time_s": 0.01},
            "started_at": started,
            "finished_at": started + 0.01,
        }

    monkeypatch.setattr(serve_support, "_execute_playground_request", _fake_execute)

    manager = PlaygroundJobManager()
    manager.ensure_value_job(
        {
            "program": "x = 1",
            "execute": True,
            "execution_strategy": "dask",
            "_job_kind": "value-resolve",
            "_priority_node": "node-1",
            "_program_hash": "prog-1",
        },
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
    )

    job = manager.get_value_job(
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
    )
    assert job is not None
    created = manager._jobs[str(job["job_id"])]
    inspector = created.runtime_inspectors["node-1"]
    monkeypatch.setattr(inspector, "preview", lambda **kwargs: (_ for _ in ()).throw(KeyError("Unknown primitive: pflair")))

    preview = manager.inspect_value_job_runtime(
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
        path="/0",
    )
    assert preview is not None
    assert preview["runtime_error"] == "Unknown primitive: pflair"
    assert preview["runtime_error_type"] == "KeyError"


@pytest.mark.unit
def test_playground_manager_value_resolve_reports_queued_before_thread_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voxlogica.serve_support as serve_support

    monkeypatch.setattr(serve_support, "PLAYGROUND_JOB_LOG_DIR", tmp_path)

    first_started = threading.Event()
    release_first = threading.Event()

    def _fake_execute(
        request_payload: dict[str, object],
        log_path_str: str,
        live_inspector: LiveRuntimeValueInspector | None = None,
    ) -> dict[str, object]:
        started = time.time()
        if str(request_payload.get("_priority_node", "")) == "node-1":
            if live_inspector is not None:
                store = MaterializationStore(backend=None, read_through=False, write_through=False)
                store.put("node-1", [10, 11, 12], metadata={"source": "runtime"})
                live_inspector.attach_materialization_store(store)
            first_started.set()
            release_first.wait(timeout=2.0)
        return {
            "ok": True,
            "result": {"execution": {"success": True, "cache_summary": {"computed": 1}}},
            "metrics": {"wall_time_s": 0.01, "cpu_time_s": 0.01},
            "started_at": started,
            "finished_at": started + 0.01,
        }

    monkeypatch.setattr(serve_support, "_execute_playground_request", _fake_execute)

    manager = PlaygroundJobManager()

    manager.ensure_value_job(
        {
            "program": "x = 1",
            "execute": True,
            "execution_strategy": "dask",
            "_job_kind": "value-resolve",
            "_priority_node": "node-1",
            "_program_hash": "prog-1",
        },
        program_hash="prog-1",
        node_id="node-1",
        execution_strategy="dask",
    )
    assert first_started.wait(timeout=1.0), "first value job did not start"

    second = manager.ensure_value_job(
        {
            "program": "y = 2",
            "execute": True,
            "execution_strategy": "dask",
            "_job_kind": "value-resolve",
            "_priority_node": "node-2",
            "_program_hash": "prog-1",
        },
        program_hash="prog-1",
        node_id="node-2",
        execution_strategy="dask",
    )
    assert second["status"] == "queued"

    queued = manager.get_value_job(
        program_hash="prog-1",
        node_id="node-2",
        execution_strategy="dask",
    )
    assert queued is not None
    assert queued["status"] == "queued"

    release_first.set()
    deadline = time.time() + 2.0
    while time.time() < deadline:
        final = manager.get_value_job(
            program_hash="prog-1",
            node_id="node-2",
            execution_strategy="dask",
        )
        if final and final["status"] == "completed":
            break
        time.sleep(0.01)
    else:
        pytest.fail("second value job did not complete after queue release")


@pytest.mark.unit
def test_playground_manager_inspects_live_runtime_for_running_value_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voxlogica.serve_support as serve_support

    monkeypatch.setattr(serve_support, "PLAYGROUND_JOB_LOG_DIR", tmp_path)

    started = threading.Event()
    release = threading.Event()

    def _fake_execute(
        request_payload: dict[str, object],
        log_path_str: str,
        live_inspector: LiveRuntimeValueInspector | None = None,
    ) -> dict[str, object]:
        del request_payload, log_path_str
        store = MaterializationStore(backend=None, read_through=False, write_through=False)
        store.put("node-live", InspectableRangeSequence(parent_ref="node-live", start=80, stop=82), metadata={"source": "runtime"})
        if live_inspector is not None:
            live_inspector.attach_materialization_store(store)
        started.set()
        release.wait(timeout=2.0)
        finished = time.time()
        return {
            "ok": True,
            "result": {"execution": {"success": True}},
            "_runtime_goal_values": {"node-live": [80, 81]},
            "metrics": {"wall_time_s": 0.01, "cpu_time_s": 0.01},
            "started_at": finished - 0.01,
            "finished_at": finished,
        }

    monkeypatch.setattr(serve_support, "_execute_playground_request", _fake_execute)

    manager = PlaygroundJobManager()
    manager.ensure_value_job(
        {
            "program": "xs = range(80,82)",
            "execute": True,
            "execution_strategy": "dask",
            "_job_kind": "value-resolve",
            "_priority_node": "node-live",
            "_program_hash": "prog-live",
        },
        program_hash="prog-live",
        node_id="node-live",
        execution_strategy="dask",
    )

    try:
        assert started.wait(timeout=1.0) is True
        preview = manager.inspect_value_job_runtime(
            program_hash="prog-live",
            node_id="node-live",
            execution_strategy="dask",
            path="/1",
        )
        assert preview is not None
        assert preview["value"] == 81

        page_preview = manager.inspect_value_job_runtime(
            program_hash="prog-live",
            node_id="node-live",
            execution_strategy="dask",
            path="/",
            page_offset=0,
            page_limit=2,
        )
        assert page_preview is not None
        assert page_preview["page"]["items"][0]["status"] == "ready"
        assert page_preview["page"]["items"][0]["node_id"] == hash_sequence_item("node-live", 0)
    finally:
        release.set()


@pytest.mark.unit
def test_inspect_runtime_value_page_reports_inspectable_item_states() -> None:
    sequence = InspectableRangeSequence(parent_ref="node-seq", start=80, stop=82)
    page = inspect_runtime_value_page(node_id="node-seq", value=sequence, path="/", offset=0, limit=2)
    assert page["page"]["items"][0]["status"] == "ready"
    assert page["page"]["items"][0]["state"] == "ready"
    assert page["page"]["items"][0]["node_id"] == hash_sequence_item("node-seq", 0)


@pytest.mark.unit
def test_inspect_runtime_value_page_refreshes_stale_visible_item_states() -> None:
    class _StaleSnapshotSequence(InspectableSequenceValue):
        inline_items = True

        def __init__(self) -> None:
            super().__init__(parent_ref="node-stale-page", total_size=2)

        def _compute_item(self, index: int, priority: str) -> int:
            del priority
            if index >= 2:
                raise IndexError(index)
            return 100 + index

        def page_snapshot(self, offset: int, limit: int, priority: str = "visible-page") -> dict[str, object]:
            del priority
            return {
                "offset": offset,
                "limit": limit,
                "items": [
                    ItemSnapshot(
                        index=0,
                        child_ref=self.child_ref(0),
                        state="queued",
                        state_reason="priority:visible-page",
                    )
                ],
                "next_offset": None,
                "has_more": False,
                "total": 2,
            }

    page = inspect_runtime_value_page(
        node_id="node-stale-page",
        value=_StaleSnapshotSequence(),
        path="/",
        offset=0,
        limit=1,
    )

    item = page["page"]["items"][0]
    assert item["status"] == "ready"
    assert item["state"] == "ready"
    assert item["descriptor"]["vox_type"] == "integer"


@pytest.mark.unit
def test_describe_runtime_value_preserves_blocked_inspectable_child_state() -> None:
    class _BlockedSequence(InspectableSequenceValue):
        def __init__(self) -> None:
            super().__init__(parent_ref="node-blocked", total_size=8)

        def _compute_item(self, index: int, priority: str) -> int:
            del priority
            if index == 5:
                raise BlockedComputation(blocked_on="upstream:/5", state_reason="upstream:running")
            return index

    sequence = _BlockedSequence()

    payload = describe_runtime_value(node_id="node-blocked", value=sequence, path="/5")

    assert payload["status"] in {"queued", "running", "blocked"}
    assert payload["path"] == "/5"
    assert payload["descriptor"]["vox_type"] == "unavailable"
    assert payload["error"] is None
    if payload["status"] == "blocked":
        assert payload["blocked_on"] == "upstream:/5"
        assert payload["state_reason"] == "upstream:running"


@pytest.mark.unit
def test_runtime_value_inspector_preview_does_not_report_out_of_range_for_blocked_child() -> None:
    class _BlockedSequence(InspectableSequenceValue):
        def __init__(self) -> None:
            super().__init__(parent_ref="node-blocked-preview", total_size=8)

        def _compute_item(self, index: int, priority: str) -> int:
            del priority
            if index == 5:
                raise BlockedComputation(blocked_on="upstream:/5", state_reason="upstream:running")
            return index

    inspector = RuntimeValueInspector(
        node_id="node-blocked-preview",
        value=freeze_runtime_value(_BlockedSequence()),
    )

    preview = inspector.preview(path="/5")

    assert preview["path"] == "/5"
    assert preview["status"] in {"queued", "running", "blocked"}
    assert preview["descriptor"]["vox_type"] == "unavailable"
    assert "error" not in preview


@pytest.mark.unit
def test_freeze_runtime_value_preserves_inspectable_sequences() -> None:
    sequence = InspectableRangeSequence(parent_ref="node-freeze", start=10, stop=13)
    frozen = freeze_runtime_value(sequence)

    assert frozen is sequence


@pytest.mark.unit
def test_inspect_runtime_value_page_uses_root_node_for_runtime_overlay_render_urls() -> None:
    overlay = OverlayValue.from_layers(
        [
            np.zeros((2, 2, 2), dtype=np.float32),
            np.ones((2, 2, 2), dtype=np.float32),
        ]
    )
    sequence = InspectableListSequence(parent_ref="node-overlay-seq", values=[overlay])

    page = inspect_runtime_value_page(node_id="node-overlay-seq", value=sequence, path="/", offset=0, limit=1)

    item = page["page"]["items"][0]
    assert item["node_id"] == hash_sequence_item("node-overlay-seq", 0)
    render = item["descriptor"]["render"]
    assert render["kind"] == "medical-overlay"
    assert render["layers"][0]["nifti_url"].startswith("/api/v1/results/store/node-overlay-seq/render/nii?path=%2F0%2F0")
    assert render["layers"][1]["nifti_url"].startswith("/api/v1/results/store/node-overlay-seq/render/nii?path=%2F0%2F1")


@pytest.mark.unit
def test_runtime_value_inspector_memoizes_nested_sequence_paths() -> None:
    outer_calls = {"count": 0}
    inner_calls = {"count": 0}

    def _inner_sequence(seed: int) -> SequenceValue:
        def _inner_iter():
            inner_calls["count"] += 1
            for offset in range(3):
                yield seed + offset

        return SequenceValue(_inner_iter, total_size=3)

    def _outer_iter():
        outer_calls["count"] += 1
        yield _inner_sequence(10)
        yield _inner_sequence(20)

    inspector = RuntimeValueInspector(
        node_id="node-nested",
        value=freeze_runtime_value(SequenceValue(_outer_iter, total_size=2)),
    )

    first = inspector.preview(path="/0/2")
    second = inspector.preview(path="/0/1")
    page = inspector.preview(path="/0", page_offset=0, page_limit=3)

    assert first["value"] == 12
    assert second["value"] == 11
    assert page["page"]["items"][0]["path"] == "/0/0"
    assert outer_calls["count"] == 1
    assert inner_calls["count"] == 1


@pytest.mark.unit
def test_playground_program_library_uses_fixed_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    load_dir = tmp_path / "playground-programs"
    load_dir.mkdir(parents=True)
    (load_dir / "demo.imgql").write_text("answer = 1", encoding="utf-8")
    monkeypatch.setenv("VOXLOGICA_SERVE_LOAD_DIR", str(load_dir))

    listing = list_playground_programs(limit=20)
    assert listing["available"] is True
    assert listing["load_dir"] == str(load_dir.resolve())
    assert [entry["path"] for entry in listing["files"]] == ["demo.imgql"]

    loaded = load_playground_program("demo.imgql")
    assert loaded["path"] == "demo.imgql"
    assert loaded["content"].strip() == "answer = 1"

    with pytest.raises(ValueError):
        load_playground_program("../outside.imgql")


@pytest.mark.unit
def test_store_results_snapshot_and_inspection_describe_types(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success("node-map", {"nums": [1, 2, 3], "label": "hello"})
    db.put_success("node-arr2d", np.arange(9, dtype=np.float32).reshape(3, 3))
    db.put_success("node-arr3d", np.arange(27, dtype=np.float32).reshape(3, 3, 3))
    try:
        snapshot = list_store_results_snapshot(db, limit=10)
        assert snapshot["available"] is True
        assert snapshot["summary"]["materialized"] == 3

        root = inspect_store_result(db, node_id="node-map")
        assert root["status"] == "materialized"
        assert root["descriptor"]["vox_type"] == "mapping"

        arr2d = inspect_store_result(db, node_id="node-arr2d")
        assert arr2d["descriptor"]["vox_type"] == "ndarray"
        assert arr2d["descriptor"]["render"]["kind"] == "image2d"

        arr3d = inspect_store_result(db, node_id="node-arr3d")
        assert arr3d["descriptor"]["render"]["kind"] == "medical-volume"

        nums = inspect_store_result(db, node_id="node-map", path="/nums")
        assert nums["descriptor"]["vox_type"] == "sequence"
        nums_page = inspect_store_result_page(db, node_id="node-map", path="/nums", offset=0, limit=2)
        assert nums_page["descriptor"]["vox_type"] == "sequence"
        assert len(nums_page["page"]["items"]) == 2
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
        nii_plain = render_store_result_nifti(db, node_id="node-nii")
        nii = render_store_result_nifti_gz(db, node_id="node-nii")
    finally:
        db.close()

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(nii_plain) > 352
    assert nii_plain[:4] in {bytes([92, 1, 0, 0]), bytes([0, 0, 1, 92])}
    assert nii.startswith(b"\x1f\x8b")


@pytest.mark.unit
def test_sequence_pages_preserve_non_json_items_for_descriptor_and_rendering(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("SimpleITK")
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    sequence = [
        np.arange(64, dtype=np.float32).reshape(4, 4, 4),
        np.ones((4, 4, 4), dtype=np.float32),
    ]
    db.put_success("node-seq-vol", sequence)
    try:
        root_record = db.get_record("node-seq-vol")
        assert root_record is not None
        assert root_record.payload_json.get("encoding") == "sequence-node-refs-v1"

        page_payload = inspect_store_result_page(db, node_id="node-seq-vol", offset=0, limit=2)
        page_items = page_payload["page"]["items"]
        assert len(page_items) == 2
        assert page_items[0]["path"] == "/0"
        assert page_items[0]["node_id"] == hash_sequence_item("node-seq-vol", 0)
        assert page_items[0]["descriptor"]["vox_type"] == "ndarray"
        assert page_items[0]["descriptor"]["render"]["kind"] == "medical-volume"

        chunk = db.get_page_containing_index("node-seq-vol", "", 0)
        assert isinstance(chunk, dict)
        first_raw = chunk["items"][0]
        first_ref = first_raw.get("__vox_ref__", {}) if isinstance(first_raw, dict) else {}
        assert first_ref.get("node_id") == hash_sequence_item("node-seq-vol", 0)

        child_record = db.get_record(hash_sequence_item("node-seq-vol", 0))
        assert child_record is not None
        assert child_record.vox_type == "ndarray"

        first_item = inspect_store_result(db, node_id="node-seq-vol", path="/0")
        assert first_item["path"] == "/0"
        assert first_item["descriptor"]["vox_type"] == "ndarray"
        assert first_item["descriptor"]["render"]["kind"] == "medical-volume"

        nii = render_store_result_nifti_gz(db, node_id="node-seq-vol", path="/0")
        assert nii.startswith(b"\x1f\x8b")
    finally:
        db.close()


@pytest.mark.unit
def test_nested_inspectable_sequence_persistence_preserves_child_pages_and_overlay_rendering(
    tmp_path: Path,
) -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("SimpleITK")
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    overlay = OverlayValue.from_layers(
        [
            np.zeros((4, 4, 4), dtype=np.float32),
            np.ones((4, 4, 4), dtype=np.float32),
        ]
    )
    nested = InspectableListSequence(
        parent_ref="outer-seq",
        values=[InspectableListSequence(parent_ref="outer-seq:0", values=[overlay])],
    )
    db.put_success("outer-seq", nested)
    try:
        child_node_id = hash_sequence_item("outer-seq", 0)
        child_record = db.get_record(child_node_id)
        assert child_record is not None
        assert child_record.vox_type == "sequence"
        assert child_record.payload_json.get("encoding") == "sequence-node-refs-v1"
        assert child_record.payload_json.get("length") == 1

        child_page = inspect_store_result_page(db, node_id=child_node_id, offset=0, limit=1)
        nested_item = child_page["page"]["items"][0]
        assert nested_item["descriptor"]["vox_type"] == "overlay"

        nii = render_store_result_nifti_gz(db, node_id="outer-seq", path="/0/0/0")
        assert nii.startswith(b"\x1f\x8b")
    finally:
        db.close()


class _DuckSimpleITKImage:
    def GetDimension(self) -> int:
        return 3

    def GetSize(self) -> tuple[int, int, int]:
        return (8, 6, 4)

    def GetSpacing(self) -> tuple[float, float, float]:
        return (1.0, 1.0, 2.0)

    def GetOrigin(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)

    def GetDirection(self) -> tuple[float, ...]:
        return (
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
        )

    def GetPixelIDTypeAsString(self) -> str:
        return "32-bit float"


@pytest.mark.unit
def test_describe_runtime_value_detects_simpleitk_duck_typed_images() -> None:
    payload = describe_runtime_value(
        node_id="node-duck",
        value={"image": _DuckSimpleITKImage()},
        path="/image",
    )
    descriptor = payload["descriptor"]
    assert descriptor["vox_type"] == "volume3d"
    assert descriptor["summary"]["dimension"] == 3
    assert descriptor["summary"]["size"] == [8, 6, 4]


@pytest.mark.unit
def test_descriptors_do_not_leak_python_runtime_type_names(tmp_path: Path) -> None:
    import json as _json

    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success("node-seq", [1, 2, 3, 4])
    try:
        payload = inspect_store_result(db, node_id="node-seq")
        rendered = _json.dumps(payload)
        assert "dask.bag" not in rendered
        assert "voxlogica.execution_strategy" not in rendered
    finally:
        db.close()
