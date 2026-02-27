from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import typer

from voxlogica.features import OperationResult
from voxlogica import main as main_mod
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_feature_or_exit_unknown():
    with pytest.raises(typer.Exit):
        main_mod._feature_or_exit("does-not-exist")


@pytest.mark.unit
def test_handle_cli_result_failure_raises_exit():
    with pytest.raises(typer.Exit):
        main_mod._handle_cli_result("run", OperationResult.fail("boom"))


@pytest.mark.unit
def test_run_command_success_and_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "program.imgql"
    src.write_text('print "x" 1', encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_handler(**kwargs):
        captured.update(kwargs)
        return OperationResult.ok({"operations": 1, "goals": 1})

    monkeypatch.setattr(
        main_mod,
        "_feature_or_exit",
        lambda _name: SimpleNamespace(handler=fake_handler),
    )

    main_mod.run(str(src), execute=False, execution_strategy="dask")
    assert captured["filename"] == str(src)
    assert captured["execute"] is False
    assert captured["execution_strategy"] == "dask"
    assert captured["legacy"] is False

    with pytest.raises(typer.Exit):
        main_mod.run(str(src), execute=False, execution_strategy="strict")

    captured.clear()
    main_mod.run(str(src), execute=False, execution_strategy="dask", legacy=True)
    assert captured["legacy"] is True

    with pytest.raises(typer.Exit):
        main_mod.run(str(tmp_path / "missing.imgql"))


@pytest.mark.unit
def test_list_primitives_and_repl_and_serve(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        main_mod,
        "handle_list_primitives",
        lambda namespace=None: OperationResult.ok(
            {
                "primitives": {"default.addition": "plus"},
                "namespaces": ["default", "simpleitk"],
                "namespace_filter": namespace,
            }
        ),
    )
    main_mod.list_primitives(None)
    out = capsys.readouterr().out
    assert "All available primitives" in out
    assert "default.addition" in out

    monkeypatch.setattr(main_mod, "run_interactive_repl", lambda strategy, legacy=False: 7)
    with pytest.raises(typer.Exit):
        main_mod.repl(execution_strategy="strict")

    called: dict[str, object] = {}
    monkeypatch.setattr(
        main_mod.uvicorn,
        "run",
        lambda app, host, port: called.update({"app": app, "host": host, "port": port}),
    )
    main_mod.serve(host="127.0.0.1", port=9001, debug=False)
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 9001


@pytest.mark.unit
def test_dev_supervisor_prefixes_repo_pythonpath(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(main_mod, "setup_logging", lambda debug, verbose: None)
    monkeypatch.setattr(main_mod, "_terminate_child_process", lambda proc, name: None)
    monkeypatch.setattr(main_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(main_mod.subprocess, "run", lambda *args, **kwargs: None)

    captured_envs: list[dict[str, str]] = []

    class _FakeProc:
        def poll(self):
            return 0

    def _fake_popen(cmd, cwd=None, env=None, **kwargs):  # noqa: ANN001
        captured_envs.append(dict(env or {}))
        return _FakeProc()

    monkeypatch.setattr(main_mod.subprocess, "Popen", _fake_popen)

    with pytest.raises(typer.Exit):
        main_mod.dev()

    assert captured_envs
    backend_env = captured_envs[0]
    expected_prefix = str((Path(main_mod.__file__).resolve().parents[3] / "implementation" / "python").resolve())
    pythonpath = backend_env.get("PYTHONPATH", "")
    assert pythonpath
    assert pythonpath.split(os.pathsep)[0] == expected_prefix


@pytest.mark.unit
def test_api_endpoints_and_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    library = tmp_path / "playground-library"
    library.mkdir(parents=True)
    (library / "demo.imgql").write_text('print "demo" 1', encoding="utf-8")
    monkeypatch.setenv("VOXLOGICA_SERVE_LOAD_DIR", str(library))

    class FakeRegistry:
        @staticmethod
        def get_feature(name: str):
            if name == "version":
                return SimpleNamespace(handler=lambda: OperationResult.ok({"version": "2.0.0"}))
            if name == "run":
                return SimpleNamespace(handler=lambda **kwargs: OperationResult.ok({"ok": True, "args": kwargs}))
            return None

    monkeypatch.setattr(main_mod, "FeatureRegistry", FakeRegistry)
    fake_storage = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    monkeypatch.setattr(main_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(main_mod, "handle_list_primitives", lambda namespace=None: OperationResult.ok({"n": namespace}))
    created_job_payloads: list[dict[str, object]] = []

    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            create_job=lambda payload: (created_job_payloads.append(dict(payload)), {"job_id": "p1", "status": "running"})[1],
            list_jobs=lambda: {"jobs": [], "total_jobs": 0, "generated_at": "-"},
            get_job=lambda job_id: {"job_id": job_id, "status": "completed", "result": {}, "log_tail": ""},
            kill_job=lambda job_id: {"job_id": job_id, "status": "killed", "log_tail": ""},
            get_value_job=lambda **kwargs: None,
            ensure_value_job=lambda payload, **kwargs: {"job_id": "vp1", "status": "running", "log_tail": ""},
        ),
    )
    monkeypatch.setattr(
        main_mod,
        "testing_jobs",
        SimpleNamespace(
            create_job=lambda profile, include_perf: {"job_id": "t1", "status": "running", "profile": profile, "include_perf": include_perf},
            list_jobs=lambda: {"jobs": []},
            get_job=lambda job_id: {"job_id": job_id, "status": "completed", "log_tail": ""},
            kill_job=lambda job_id: {"job_id": job_id, "status": "killed", "log_tail": ""},
        ),
    )
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    try:
        with TestClient(main_mod.api_app) as client:
            version_resp = client.get("/api/v1/version")
            assert version_resp.status_code == 200
            assert version_resp.json()["version"] == "2.0.0"

            run_resp = client.post("/api/v1/run", json={"program": 'print "x" 1'})
            assert run_resp.status_code == 200
            assert run_resp.json()["ok"] is True
            run_args = run_resp.json()["args"]
            assert run_args["execution_strategy"] == "dask"
            assert run_args["legacy"] is False
            assert run_args["serve_mode"] is True

            primitives_resp = client.get("/api/v1/primitives", params={"namespace": "default"})
            assert primitives_resp.status_code == 200
            assert primitives_resp.json()["n"] == "default"

            docs_resp = client.get("/api/v1/docs/gallery")
            assert docs_resp.status_code == 200
            assert "examples" in docs_resp.json()

            files_resp = client.get("/api/v1/playground/files")
            assert files_resp.status_code == 200
            files_payload = files_resp.json()
            assert files_payload["available"] is True
            assert files_payload["files"][0]["path"] == "demo.imgql"
            file_resp = client.get("/api/v1/playground/files/demo.imgql")
            assert file_resp.status_code == 200
            assert 'print "demo" 1' in file_resp.json()["content"]

            blocked_run = client.post("/api/v1/run", json={"program": 'print "x" 1', "save_task_graph": "/tmp/x.dot"})
            assert blocked_run.status_code == 400

            symbols_resp = client.post("/api/v1/playground/symbols", json={"program": "x = 2 + 3"})
            assert symbols_resp.status_code == 200
            symbol_payload = symbols_resp.json()
            assert symbol_payload["available"] is True
            assert "x" in symbol_payload["symbol_table"]
            assert symbol_payload["print_targets"] == []
            node_id = symbol_payload["symbol_table"]["x"]

            value_pending = client.post(
                "/api/v1/playground/value",
                json={"program": "x = 2 + 3", "variable": "x", "execution_strategy": "strict"},
            )
            assert value_pending.status_code == 200
            assert value_pending.json()["materialization"] == "pending"
            assert value_pending.json()["compute_status"] in {"queued", "running"}
            assert value_pending.json()["execution_strategy"] == "dask"

            # Variable lookup must win over stale/foreign node ids.
            value_with_stale_node = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "x = 2 + 3",
                    "variable": "x",
                    "node_id": "deadbeef",
                    "execution_strategy": "strict",
                },
            )
            assert value_with_stale_node.status_code == 200
            assert value_with_stale_node.json()["node_id"] == node_id

            fake_storage.put_success(node_id, 5)
            value_cached = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "x = 2 + 3",
                    "variable": "x",
                    "execution_strategy": "strict",
                    "enqueue": False,
                },
            )
            assert value_cached.status_code == 200
            assert value_cached.json()["materialization"] == "cached"
            assert value_cached.json()["descriptor"]["vox_type"] == "integer"
            assert value_cached.json()["descriptor"]["summary"]["value"] == 5

            page_cached = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "x = 2 + 3",
                    "variable": "x",
                    "offset": 0,
                    "limit": 8,
                    "enqueue": False,
                },
            )
            assert page_cached.status_code == 400

            symbols_seq = client.post("/api/v1/playground/symbols", json={"program": "xs = range(0,5)"})
            assert symbols_seq.status_code == 200
            xs_node = symbols_seq.json()["symbol_table"]["xs"]
            fake_storage.put_success(xs_node, [0, 1, 2, 3, 4])
            page_seq = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(0,5)",
                    "variable": "xs",
                    "offset": 1,
                    "limit": 2,
                    "enqueue": False,
                },
            )
            assert page_seq.status_code == 200
            page_payload = page_seq.json()
            assert page_payload["descriptor"]["vox_type"] == "sequence"
            assert page_payload["page"]["offset"] == 1
            assert len(page_payload["page"]["items"]) == 2

            invalid_node = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "x = 2 + 3",
                    "node_id": "deadbeef",
                    "execution_strategy": "strict",
                    "enqueue": False,
                },
            )
            assert invalid_node.status_code == 400

            jobs_resp = client.get("/api/v1/playground/jobs")
            assert jobs_resp.status_code == 200
            assert "jobs" in jobs_resp.json()
            created = client.post("/api/v1/playground/jobs", json={"program": 'print "x" 1'})
            assert created.status_code == 200
            assert created.json()["status"] == "running"
            assert created_job_payloads
            assert created_job_payloads[-1]["execution_strategy"] == "dask"
            assert created_job_payloads[-1]["legacy"] is False
            assert created_job_payloads[-1]["serve_mode"] is True
            blocked_job = client.post(
                "/api/v1/playground/jobs",
                json={"program": 'print "x" 1', "save_syntax": "/tmp/syntax.txt"},
            )
            assert blocked_job.status_code == 400

            test_report_resp = client.get("/api/v1/testing/report")
            assert test_report_resp.status_code == 200
            assert "junit" in test_report_resp.json()
            primitive_chart_resp = client.get("/api/v1/testing/performance/primitive-chart")
            assert primitive_chart_resp.status_code in {200, 404}

            storage_resp = client.get("/api/v1/storage/stats")
            assert storage_resp.status_code == 200
            assert "available" in storage_resp.json()
            results_resp = client.get("/api/v1/results/store")
            assert results_resp.status_code == 200
            assert "available" in results_resp.json()
            page_resp = client.get("/api/v1/results/store/missing-node/page")
            assert page_resp.status_code == 404
            assert client.get("/api/v1/results/store/missing-node").status_code == 404

            test_job_start = client.post("/api/v1/testing/jobs", json={"profile": "quick", "include_perf": False})
            assert test_job_start.status_code == 200
            assert test_job_start.json()["status"] == "running"

            test_jobs = client.get("/api/v1/testing/jobs")
            assert test_jobs.status_code == 200
            assert "jobs" in test_jobs.json()

            test_job = client.get("/api/v1/testing/jobs/t1")
            assert test_job.status_code == 200
            assert test_job.json()["status"] == "completed"

            root_resp = client.get("/")
            assert root_resp.status_code == 200
            assert root_resp.headers.get("cache-control") == "no-store"
            assert "__ASSET_REV__" not in root_resp.text
            assert "/static/results_viewer.js?v=" in root_resp.text
            static_js_resp = client.get("/static/results_viewer.js")
            assert static_js_resp.status_code == 200
            assert static_js_resp.headers.get("cache-control") == "no-store"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_failed_job_exposes_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class FakeRegistry:
        @staticmethod
        def get_feature(name: str):
            if name == "version":
                return SimpleNamespace(handler=lambda: OperationResult.ok({"version": "2.0.0"}))
            if name == "run":
                return SimpleNamespace(handler=lambda **kwargs: OperationResult.ok({"ok": True, "args": kwargs}))
            return None

    monkeypatch.setattr(main_mod, "FeatureRegistry", FakeRegistry)
    fake_storage = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    monkeypatch.setattr(main_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)
    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            get_value_job=lambda **kwargs: {
                "job_id": "value-failed-1",
                "status": "failed",
                "error": "Execution failed with 1 errors",
                "log_tail": '{"event":"playground.node","status":"failed","node_id":"abc","error":"kernel boom"}',
                "result": {
                    "execution": {
                        "errors": {"abc123deadbeef": "kernel boom"},
                        "error_details": {
                            "abc123deadbeef": {
                                "operator": "default.load",
                                "args": ["source_node_1"],
                                "kwargs": {},
                                "attrs": {},
                            }
                        },
                        "cache_summary": {"failed": 1, "events_total": 1, "events_stored": 1},
                    }
                },
            },
            ensure_value_job=lambda payload, **kwargs: {"job_id": "should-not-enqueue", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            symbols_resp = client.post("/api/v1/playground/symbols", json={"program": "let x = 2 + 3"})
            assert symbols_resp.status_code == 200
            assert "x" in symbols_resp.json()["symbol_table"]

            value_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "let x = 2 + 3",
                    "variable": "x",
                    "execution_strategy": "strict",
                },
            )
            assert value_resp.status_code == 200
            payload = value_resp.json()
            assert payload["materialization"] == "failed"
            assert payload["compute_status"] == "failed"
            assert payload["request_enqueued"] is False
            assert payload["job_id"] == "value-failed-1"
            assert "Execution failed with 1 errors" in str(payload["error"])
            assert payload["execution_errors"]["abc123deadbeef"] == "kernel boom"
            assert payload["execution_error_details"]["abc123deadbeef"]["operator"] == "default.load"
            assert "playground.node" in str(payload.get("log_tail", ""))
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_completed_pending_persistence_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class FakeRegistry:
        @staticmethod
        def get_feature(name: str):
            if name == "version":
                return SimpleNamespace(handler=lambda: OperationResult.ok({"version": "2.0.0"}))
            if name == "run":
                return SimpleNamespace(handler=lambda **kwargs: OperationResult.ok({"ok": True, "args": kwargs}))
            return None

    monkeypatch.setattr(main_mod, "FeatureRegistry", FakeRegistry)
    fake_storage = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    monkeypatch.setattr(main_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    pending_finished_at = datetime.now(timezone.utc).isoformat()
    stale_finished_at = (datetime.now(timezone.utc) - timedelta(seconds=8)).isoformat()

    def _make_completed_job(finished_at: str):
        def _get_value_job(**kwargs):
            node_id = str(kwargs.get("node_id", "unknown"))
            return {
                "job_id": "value-completed-1",
                "status": "completed",
                "finished_at": finished_at,
                "log_tail": "",
                "result": {
                    "goal_results": [
                        {
                            "node_id": node_id,
                            "runtime_descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 18},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
                            "metadata": {"persisted": "pending"},
                        }
                    ]
                },
            }

        return _get_value_job

    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            get_value_job=_make_completed_job(pending_finished_at),
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            pending_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "let x = range(1,19)",
                    "variable": "x",
                    "enqueue": False,
                },
            )
            assert pending_resp.status_code == 200
            pending_payload = pending_resp.json()
            assert pending_payload["materialization"] == "pending"
            assert pending_payload["compute_status"] == "persisting"

            monkeypatch.setattr(
                main_mod,
                "playground_jobs",
                SimpleNamespace(
                    get_value_job=_make_completed_job(stale_finished_at),
                    ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
                ),
            )
            failed_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "let x = range(1,19)",
                    "variable": "x",
                    "enqueue": False,
                },
            )
            assert failed_resp.status_code == 200
            failed_payload = failed_resp.json()
            assert failed_payload["materialization"] == "failed"
            assert failed_payload["compute_status"] == "failed"
            assert "persistence did not finish" in str(failed_payload["error"])
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_reports_spec_error_when_value_is_not_persistable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    fake_storage = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    monkeypatch.setattr(main_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    tracked_jobs: list[dict[str, object]] = []

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-completed-transient",
            "status": "completed",
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": False, "persist_error": "Value not serializable"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {"kind": "sequence", "length": 3, "numeric_values": [1.0, 2.0, 3.0]},
                        },
                    }
                ]
            },
            "log_tail": "",
        }

    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            get_value_job=lambda **kwargs: _job_payload(str(kwargs.get("node_id", ""))),
            ensure_value_job=lambda payload, **kwargs: tracked_jobs.append(dict(payload)) or {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            symbols_resp = client.post("/api/v1/playground/symbols", json={"program": "let x = 2 + 3"})
            assert symbols_resp.status_code == 200
            node_id = symbols_resp.json()["symbol_table"]["x"]

            value_resp = client.post(
                "/api/v1/playground/value",
                json={"program": "let x = 2 + 3", "variable": "x"},
            )
            assert value_resp.status_code == 200
            payload = value_resp.json()
            assert payload["available"] is False
            assert payload["materialization"] == "failed"
            assert payload["compute_status"] == "failed"
            assert "Value not serializable" in str(payload["error"])
            assert payload["diagnostics"]["code"] in {"E_UNSPECIFIED_VALUE_TYPE", "E_PERSISTENCE_FAILED"}
            assert payload["node_id"] == node_id
            assert tracked_jobs == []

            missing_preview = client.post(
                "/api/v1/playground/value",
                json={"program": "let x = 2 + 3", "node_id": "does-not-exist", "enqueue": False},
            )
            assert missing_preview.status_code == 400
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_symbols_reports_static_diagnostics(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    with TestClient(main_mod.api_app) as client:
        unknown_resp = client.post(
            "/api/v1/playground/symbols",
            json={"program": 'let x = UnknownCallable(1)'},
        )
        assert unknown_resp.status_code == 200
        unknown_payload = unknown_resp.json()
        assert unknown_payload["available"] is False
        assert any(item.get("code") == "E_UNKNOWN_CALLABLE" for item in unknown_payload.get("diagnostics", []))

        effect_resp = client.post(
            "/api/v1/playground/symbols",
            json={
                "program": '\n'.join(
                    [
                        'import "simpleitk"',
                        'let out = WriteImage(0, "tests/output/blocked.nii.gz")',
                    ]
                )
            },
        )
        assert effect_resp.status_code == 200
        effect_payload = effect_resp.json()
        assert effect_payload["available"] is True
        assert any(item.get("code") == "E_EFFECT_BLOCKED" for item in effect_payload.get("diagnostics", []))


@pytest.mark.unit
def test_playground_symbols_enforces_read_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    allowed = tmp_path / "allowed"
    allowed.mkdir(parents=True)
    monkeypatch.setenv("VOXLOGICA_SERVE_DATA_DIR", str(allowed))
    monkeypatch.delenv("VOXLOGICA_SERVE_EXTRA_READ_ROOTS", raising=False)

    outside = tmp_path / "outside.nii.gz"
    with TestClient(main_mod.api_app) as client:
        resp = client.post(
            "/api/v1/playground/symbols",
            json={
                "program": '\n'.join(
                    [
                        'import "simpleitk"',
                        f'let img = ReadImage("{outside}")',
                    ]
                )
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["available"] is True
        assert any(item.get("code") == "E_READ_ROOT_POLICY" for item in payload.get("diagnostics", []))


@pytest.mark.unit
def test_api_error_paths(monkeypatch: pytest.MonkeyPatch):
    class MissingRegistry:
        @staticmethod
        def get_feature(_name: str):
            return None

    monkeypatch.setattr(main_mod, "FeatureRegistry", MissingRegistry)
    monkeypatch.setattr(
        main_mod,
        "testing_jobs",
        SimpleNamespace(
            create_job=lambda profile, include_perf: (_ for _ in ()).throw(ValueError("bad profile")),
            list_jobs=lambda: {"jobs": []},
            get_job=lambda job_id: None,
            kill_job=lambda job_id: None,
        ),
    )
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    with TestClient(main_mod.api_app) as client:
        assert client.get("/api/v1/version").status_code == 404
        assert client.post("/api/v1/run", json={"program": "x"}).status_code == 404
        assert client.post("/api/v1/testing/jobs", json={"profile": "unknown"}).status_code == 400
        assert client.get("/api/v1/testing/jobs/missing").status_code == 404
        assert client.delete("/api/v1/testing/jobs/missing").status_code == 404

    monkeypatch.setattr(main_mod, "handle_list_primitives", lambda namespace=None: OperationResult.fail("bad"))
    with TestClient(main_mod.api_app) as client:
        assert client.get("/api/v1/primitives").status_code == 400


@pytest.mark.unit
def test_reload_handler_and_livereload_websocket(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    class FakeSocket:
        def __init__(self, should_fail: bool = False):
            self.should_fail = should_fail
            self.sent: list[str] = []

        async def send_text(self, text: str):
            if self.should_fail:
                raise RuntimeError("send failed")
            self.sent.append(text)

    ok = FakeSocket()
    bad = FakeSocket(should_fail=True)
    main_mod.live_reload_clients.clear()
    main_mod.live_reload_clients.add(ok)  # type: ignore[arg-type]
    main_mod.live_reload_clients.add(bad)  # type: ignore[arg-type]

    loop = asyncio.new_event_loop()
    try:
        handler = main_mod.ReloadEventHandler(loop)
        loop.run_until_complete(handler._notify_clients())
        assert ok.sent == ["reload"]
    finally:
        loop.close()
        main_mod.live_reload_clients.clear()

    with TestClient(main_mod.api_app) as client:
        with client.websocket_connect("/livereload") as ws:
            ws.send_text('{"type":"warn","message":"w"}')
            ws.send_text('{"type":"error","message":"e"}')
            ws.send_text('{"type":"info","message":"i"}')
            ws.send_text("not-json")
