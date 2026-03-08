from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import typer

from voxlogica.features import OperationResult
from voxlogica.lazy.hash import hash_sequence_item
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
    assert captured["fresh"] is False

    with pytest.raises(typer.Exit):
        main_mod.run(str(src), execute=False, execution_strategy="strict")

    captured.clear()
    main_mod.run(str(src), execute=False, execution_strategy="dask", legacy=True, fresh=True)
    assert captured["legacy"] is True
    assert captured["fresh"] is True

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
    value_job_payloads: list[dict[str, object]] = []

    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            create_job=lambda payload: (created_job_payloads.append(dict(payload)), {"job_id": "p1", "status": "running"})[1],
            list_jobs=lambda: {"jobs": [], "total_jobs": 0, "generated_at": "-"},
            get_job=lambda job_id: {"job_id": job_id, "status": "completed", "result": {}, "log_tail": ""},
            kill_job=lambda job_id: {"job_id": job_id, "status": "killed", "log_tail": ""},
            get_value_job=lambda **kwargs: None,
            ensure_value_job=lambda payload, **kwargs: (
                value_job_payloads.append(dict(payload)),
                {"job_id": "vp1", "status": "running", "log_tail": ""},
            )[1],
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
            caps_resp = client.get("/api/v1/capabilities")
            assert caps_resp.status_code == 200
            assert caps_resp.json()["client_logging"] is True
            assert caps_resp.json()["storage_stats"] is False
            assert caps_resp.json()["storage_stats_lightweight"] is True
            log_resp = client.post(
                "/api/v1/log/client",
                json={
                    "events": [
                        {
                            "level": "warn",
                            "message": "browser test warning",
                            "source": "unit-test",
                        }
                    ]
                },
            )
            assert log_resp.status_code == 200
            assert log_resp.json()["ok"] is True
            assert log_resp.json()["accepted"] == 1

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
            assert value_job_payloads
            assert value_job_payloads[-1]["_goal_path"] in {"", "/"}

            value_job_payloads.clear()
            literal_constant = client.post(
                "/api/v1/playground/value",
                json={"program": "k = 10", "variable": "k", "execution_strategy": "strict"},
            )
            assert literal_constant.status_code == 200
            literal_payload = literal_constant.json()
            assert literal_payload["materialization"] == "computed"
            assert literal_payload["compute_status"] == "computed"
            assert literal_payload["descriptor"]["vox_type"] == "integer"
            assert literal_payload["descriptor"]["summary"]["value"] == 10
            assert literal_payload["store_status"] == "ephemeral"
            assert value_job_payloads == []

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
            assert created_job_payloads[-1].get("_job_kind") is None
            assert created_job_payloads[-1].get("_background_fill") is None

            background_created = client.post(
                "/api/v1/playground/jobs",
                json={
                    "program": "a = 1\nb = a + 2",
                    "background_fill": True,
                },
            )
            assert background_created.status_code == 200
            assert created_job_payloads[-1]["_job_kind"] == "background-fill"
            assert created_job_payloads[-1]["_background_fill"] is True
            assert created_job_payloads[-1]["_include_goal_descriptors"] is True
            background_goals = created_job_payloads[-1]["_goals"]
            assert isinstance(background_goals, list)
            assert len(background_goals) >= 2
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
def test_playground_job_background_fill_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
    created_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            create_job=lambda payload: (created_payloads.append(dict(payload)), {"job_id": "p-bg", "status": "queued"})[1],
            list_jobs=lambda: {"jobs": [], "total_jobs": 0, "generated_at": "-"},
            get_job=lambda job_id: {"job_id": job_id, "status": "completed", "result": {}, "log_tail": ""},
            kill_job=lambda job_id: {"job_id": job_id, "status": "killed", "log_tail": ""},
            get_value_job=lambda **kwargs: None,
            ensure_value_job=lambda payload, **kwargs: {"job_id": "vp1", "status": "running", "log_tail": ""},
        ),
    )
    try:
        with TestClient(main_mod.api_app) as client:
            response = client.post(
                "/api/v1/playground/jobs",
                json={
                    "program": "a = 1\nb = a + 2",
                    "background_fill": True,
                },
            )
            assert response.status_code == 200
            assert created_payloads
            payload = created_payloads[-1]
            assert payload["execution_strategy"] == "dask"
            assert payload["_job_kind"] == "background-fill"
            assert payload["_background_fill"] is True
            assert payload["_include_goal_descriptors"] is True
            goals = payload.get("_goals")
            assert isinstance(goals, list)
            assert len(goals) >= 2
            assert all(isinstance(goal, str) and goal for goal in goals)
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
    stale_finished_at = (datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat()
    enqueued_payloads: list[dict[str, object]] = []

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
                    ensure_value_job=lambda payload, **kwargs: enqueued_payloads.append(dict(payload))
                    or {"job_id": "requeued-job", "status": "running", "log_tail": ""},
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
            assert failed_payload["materialization"] == "pending"
            assert failed_payload["compute_status"] == "persisting"
            diagnostics = failed_payload.get("diagnostics", {})
            assert isinstance(diagnostics, dict)
            assert diagnostics.get("store_status") == "missing"
            assert isinstance(diagnostics.get("persistence_elapsed_s"), (int, float))
            assert enqueued_payloads == []

            requeued_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "let x = range(1,19)",
                    "variable": "x",
                },
            )
            assert requeued_resp.status_code == 200
            requeued_payload = requeued_resp.json()
            assert requeued_payload["materialization"] == "pending"
            assert requeued_payload["compute_status"] == "persisting"
            assert requeued_payload["request_enqueued"] is False
            assert requeued_payload["job_id"] == "value-completed-1"
            assert enqueued_payloads == []
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_paging_works_while_sequence_is_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-persisting-seq",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 5},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
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
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            symbols = client.post("/api/v1/playground/symbols", json={"program": "xs = range(0,5)"})
            assert symbols.status_code == 200
            node_id = symbols.json()["symbol_table"]["xs"]

            fake_storage.put_success(hash_sequence_item(node_id, 0), 11)
            fake_storage.put_success(hash_sequence_item(node_id, 1), 22)

            page_resp = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(0,5)",
                    "variable": "xs",
                    "offset": 0,
                    "limit": 4,
                    "enqueue": False,
                },
            )
            assert page_resp.status_code == 200
            page_payload = page_resp.json()
            assert page_payload["compute_status"] == "persisting"
            assert page_payload["descriptor"]["vox_type"] == "sequence"
            assert len(page_payload["page"]["items"]) == 4
            assert page_payload["page"]["items"][0]["node_id"] == hash_sequence_item(node_id, 0)
            assert page_payload["page"]["items"][0]["path"] == "/0"
            assert page_payload["page"]["items"][0]["status"] == "materialized"
            assert page_payload["page"]["items"][2]["status"] == "pending"
            assert page_payload["page"]["has_more"] is True

            item_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "xs = range(0,5)",
                    "variable": "xs",
                    "path": "/0",
                    "enqueue": False,
                },
            )
            assert item_resp.status_code == 200
            item_payload = item_resp.json()
            assert item_payload["compute_status"] == "persisting"
            assert item_payload["materialization"] == "computed"
            assert item_payload["descriptor"]["vox_type"] == "integer"
            assert item_payload["path"] == "/0"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_returns_pending_quickly_when_store_lock_is_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-persisting-lock-busy",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 2},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
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
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    lock_ready = threading.Event()
    release_lock = threading.Event()

    def _hold_store_lock() -> None:
        acquired = fake_storage._lock.acquire(timeout=1.0)  # noqa: SLF001 - test-only lock contention
        if not acquired:
            return
        try:
            lock_ready.set()
            release_lock.wait(timeout=5.0)
        finally:
            fake_storage._lock.release()  # noqa: SLF001 - test-only lock contention

    holder = threading.Thread(target=_hold_store_lock, name="hold-store-lock", daemon=True)

    try:
        with TestClient(main_mod.api_app) as client:
            symbols = client.post("/api/v1/playground/symbols", json={"program": "xs = range(0,2)"})
            assert symbols.status_code == 200
            node_id = symbols.json()["symbol_table"]["xs"]

            # Item exists in store, but read path must not block behind lock contention.
            fake_storage.put_success(hash_sequence_item(node_id, 0), 11)

            holder.start()
            assert lock_ready.wait(timeout=1.0)

            started = time.perf_counter()
            item_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "xs = range(0,2)",
                    "variable": "xs",
                    "path": "/0",
                    "enqueue": False,
                },
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            assert item_resp.status_code == 200
            payload = item_resp.json()
            assert payload["compute_status"] == "persisting"
            assert payload["materialization"] == "pending"
            assert payload["request_enqueued"] is False
            assert elapsed_ms < 1000.0
    finally:
        release_lock.set()
        holder.join(timeout=1.0)
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_pages_nested_sequence_path_while_root_is_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-persisting-nested-seq",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 2},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
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
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            symbols = client.post("/api/v1/playground/symbols", json={"program": "xs = range(0,2)"})
            assert symbols.status_code == 200
            node_id = symbols.json()["symbol_table"]["xs"]
            child_node_id = hash_sequence_item(node_id, 0)
            fake_storage.put_success(child_node_id, [7, 8])

            page_resp = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(0,2)",
                    "variable": "xs",
                    "path": "/0",
                    "offset": 0,
                    "limit": 4,
                    "enqueue": False,
                },
            )
            assert page_resp.status_code == 200
            page_payload = page_resp.json()
            assert page_payload["compute_status"] == "persisting"
            assert page_payload["descriptor"]["vox_type"] == "sequence"
            assert page_payload["path"] == "/0"
            assert page_payload["page"]["items"][0]["path"] == "/0/0"
            assert page_payload["page"]["items"][1]["path"] == "/0/1"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_page_rebases_sequence_item_paths_for_materialized_nested_sequence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    fake_storage = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    monkeypatch.setattr(main_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    root_node_id = "node-root-seq"
    child_node_id = hash_sequence_item(root_node_id, 0)
    fake_storage.put_success(child_node_id, [7, 8, 9])

    async def _fake_value_endpoint(_request):  # noqa: ANN001
        return {
            "available": True,
            "node_id": root_node_id,
            "path": "/0",
            "materialization": "cached",
            "compute_status": "cached",
            "descriptor": {
                "vox_type": "sequence",
                "format_version": "voxpod/1",
                "summary": {"length": 3},
                "navigation": {
                    "path": "/0",
                    "pageable": True,
                    "can_descend": True,
                    "default_page_size": 64,
                    "max_page_size": 512,
                },
            },
        }

    monkeypatch.setattr(main_mod, "playground_value_endpoint", _fake_value_endpoint)

    try:
        with TestClient(main_mod.api_app) as client:
            page_resp = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(0,3)",
                    "variable": "xs",
                    "path": "/0",
                    "offset": 0,
                    "limit": 2,
                    "enqueue": False,
                },
            )
            assert page_resp.status_code == 200
            page_payload = page_resp.json()
            assert page_payload["path"] == "/0"
            assert page_payload["descriptor"]["navigation"]["path"] == "/0"
            assert page_payload["page"]["items"][0]["path"] == "/0/0"
            assert page_payload["page"]["items"][1]["path"] == "/0/1"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_uses_runtime_preview_while_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-runtime-preview",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 2},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
                        },
                        "runtime_previews": {
                            "/": {
                                "path": "/",
                                "status": "materialized",
                                "descriptor": {
                                    "vox_type": "sequence",
                                    "format_version": "voxpod/1",
                                    "summary": {"length": 2},
                                    "navigation": {
                                        "path": "/",
                                        "pageable": True,
                                        "can_descend": True,
                                        "default_page_size": 64,
                                        "max_page_size": 512,
                                    },
                                },
                                "page": {
                                    "offset": 0,
                                    "limit": 64,
                                    "items": [
                                        {
                                            "index": 0,
                                            "label": "[0]",
                                            "path": "/0",
                                            "status": "materialized",
                                            "descriptor": {
                                                "vox_type": "integer",
                                                "format_version": "voxpod/1",
                                                "summary": {"value": 80},
                                                "navigation": {
                                                    "path": "/0",
                                                    "pageable": False,
                                                    "can_descend": False,
                                                    "default_page_size": 64,
                                                    "max_page_size": 512,
                                                },
                                            },
                                        },
                                        {
                                            "index": 1,
                                            "label": "[1]",
                                            "path": "/1",
                                            "status": "materialized",
                                            "descriptor": {
                                                "vox_type": "integer",
                                                "format_version": "voxpod/1",
                                                "summary": {"value": 81},
                                                "navigation": {
                                                    "path": "/1",
                                                    "pageable": False,
                                                    "can_descend": False,
                                                    "default_page_size": 64,
                                                    "max_page_size": 512,
                                                },
                                            },
                                        },
                                    ],
                                    "next_offset": None,
                                    "has_more": False,
                                    "total": 2,
                                },
                            },
                            "/0": {
                                "path": "/0",
                                "status": "materialized",
                                "value": 80,
                                "descriptor": {
                                    "vox_type": "integer",
                                    "format_version": "voxpod/1",
                                    "summary": {"value": 80},
                                    "navigation": {
                                        "path": "/0",
                                        "pageable": False,
                                        "can_descend": False,
                                        "default_page_size": 64,
                                        "max_page_size": 512,
                                    },
                                },
                            },
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
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            symbols = client.post("/api/v1/playground/symbols", json={"program": "xs = range(80,82)"})
            assert symbols.status_code == 200

            page_resp = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(80,82)",
                    "variable": "xs",
                    "path": "/",
                    "offset": 0,
                    "limit": 2,
                    "enqueue": False,
                },
            )
            assert page_resp.status_code == 200
            page_payload = page_resp.json()
            assert page_payload["compute_status"] == "persisting"
            assert page_payload["materialization"] == "computed"
            assert len(page_payload["page"]["items"]) == 2
            assert page_payload["page"]["items"][0]["status"] == "materialized"
            assert page_payload["page"]["items"][0]["path"] == "/0"

            value_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "xs = range(80,82)",
                    "variable": "xs",
                    "path": "/0",
                    "enqueue": False,
                },
            )
            assert value_resp.status_code == 200
            value_payload = value_resp.json()
            assert value_payload["compute_status"] == "persisting"
            assert value_payload["materialization"] == "computed"
            assert value_payload["descriptor"]["vox_type"] == "integer"
            assert value_payload["value"] == 80
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_uses_runtime_cache_for_completed_nested_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-runtime-cache",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 2},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
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
            inspect_value_job_runtime=lambda **kwargs: {
                "path": str(kwargs.get("path", "")),
                "status": "materialized",
                "value": 80,
                "descriptor": {
                    "vox_type": "integer",
                    "format_version": "voxpod/1",
                    "summary": {"value": 80},
                    "navigation": {
                        "path": str(kwargs.get("path", "")),
                        "pageable": False,
                        "can_descend": False,
                        "default_page_size": 64,
                        "max_page_size": 512,
                    },
                },
            },
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            value_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "xs = range(80,82)",
                    "variable": "xs",
                    "path": "/0",
                    "enqueue": False,
                },
            )
            assert value_resp.status_code == 200
            payload = value_resp.json()
            assert payload["compute_status"] == "completed"
            assert payload["materialization"] == "computed"
            assert payload["value"] == 80
            assert payload["metadata"]["source"] == "runtime-cache"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_page_uses_runtime_live_preview_while_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-runtime-live",
            "status": "running",
            "result": {},
            "log_tail": "",
        }

    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            get_value_job=lambda **kwargs: _job_payload(str(kwargs.get("node_id", ""))),
            inspect_value_job_runtime=lambda **kwargs: {
                "path": str(kwargs.get("path", "")),
                "status": "materialized",
                "descriptor": {
                    "vox_type": "sequence",
                    "format_version": "voxpod/1",
                    "summary": {"length": 2},
                    "navigation": {
                        "path": str(kwargs.get("path", "")),
                        "pageable": True,
                        "can_descend": True,
                        "default_page_size": 64,
                        "max_page_size": 512,
                    },
                },
                "page": {
                    "offset": 0,
                    "limit": 2,
                    "items": [
                        {
                            "index": 0,
                            "label": "[0]",
                            "path": "/0",
                            "node_id": "child-0",
                            "status": "ready",
                            "state": "ready",
                            "descriptor": {
                                "vox_type": "integer",
                                "format_version": "voxpod/1",
                                "summary": {"value": 80},
                                "navigation": {
                                    "path": "/0",
                                    "pageable": False,
                                    "can_descend": False,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
                        },
                        {
                            "index": 1,
                            "label": "[1]",
                            "path": "/1",
                            "node_id": "child-1",
                            "status": "ready",
                            "state": "ready",
                            "descriptor": {
                                "vox_type": "integer",
                                "format_version": "voxpod/1",
                                "summary": {"value": 81},
                                "navigation": {
                                    "path": "/1",
                                    "pageable": False,
                                    "can_descend": False,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
                        },
                    ],
                    "next_offset": None,
                    "has_more": False,
                    "total": 2,
                },
            },
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            response = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(80,82)",
                    "variable": "xs",
                    "path": "/",
                    "offset": 0,
                    "limit": 2,
                    "enqueue": False,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["compute_status"] == "running"
            assert payload["materialization"] == "computed"
            assert payload["metadata"]["source"] == "runtime-live"
            assert payload["page"]["items"][0]["status"] == "ready"
            assert payload["page"]["items"][0]["node_id"] == "child-0"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_page_uses_runtime_cache_page_for_completed_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-runtime-cache-page",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 2},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
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
            inspect_value_job_runtime=lambda **kwargs: {
                "path": "",
                "status": "materialized",
                "descriptor": {
                    "vox_type": "sequence",
                    "format_version": "voxpod/1",
                    "summary": {"length": 2},
                    "navigation": {
                        "path": "",
                        "pageable": True,
                        "can_descend": True,
                        "default_page_size": 64,
                        "max_page_size": 512,
                    },
                },
                "page": {
                    "offset": 0,
                    "limit": 64,
                    "items": [
                        {
                            "index": 0,
                            "label": "[0]",
                            "path": "/0",
                            "status": "materialized",
                            "descriptor": {
                                "vox_type": "integer",
                                "format_version": "voxpod/1",
                                "summary": {"value": 80},
                                "navigation": {
                                    "path": "/0",
                                    "pageable": False,
                                    "can_descend": False,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
                        },
                        {
                            "index": 1,
                            "label": "[1]",
                            "path": "/1",
                            "status": "materialized",
                            "descriptor": {
                                "vox_type": "integer",
                                "format_version": "voxpod/1",
                                "summary": {"value": 81},
                                "navigation": {
                                    "path": "/1",
                                    "pageable": False,
                                    "can_descend": False,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
                        },
                    ],
                    "next_offset": None,
                    "has_more": False,
                    "total": 2,
                },
            },
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            page_resp = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(80,82)",
                    "variable": "xs",
                    "offset": 0,
                    "limit": 2,
                    "enqueue": False,
                },
            )
            assert page_resp.status_code == 200
            payload = page_resp.json()
            assert payload["compute_status"] == "completed"
            assert payload["materialization"] == "computed"
            assert len(payload["page"]["items"]) == 2
            assert payload["page"]["items"][0]["path"] == "/0"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_returns_failed_payload_when_runtime_inspection_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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

    def _job_payload(node_id: str) -> dict[str, object]:
        return {
            "job_id": "value-runtime-error",
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "goal_results": [
                    {
                        "node_id": node_id,
                        "status": "materialized",
                        "metadata": {"persisted": "pending"},
                        "runtime_descriptor": {
                            "available": True,
                            "node_id": node_id,
                            "status": "materialized",
                            "runtime_version": "runtime",
                            "path": "",
                            "descriptor": {
                                "vox_type": "sequence",
                                "format_version": "voxpod/1",
                                "summary": {"length": 2},
                                "navigation": {
                                    "path": "",
                                    "pageable": True,
                                    "can_descend": True,
                                    "default_page_size": 64,
                                    "max_page_size": 512,
                                },
                            },
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
            inspect_value_job_runtime=lambda **kwargs: {
                "runtime_error": "Unknown primitive: pflair",
                "runtime_error_type": "KeyError",
                "path": str(kwargs.get("path", "")),
                "node_id": str(kwargs.get("node_id", "")),
            },
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            value_resp = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "xs = range(80,82)",
                    "variable": "xs",
                    "path": "/0",
                    "enqueue": False,
                },
            )
            assert value_resp.status_code == 200
            payload = value_resp.json()
            assert payload["compute_status"] == "failed"
            assert payload["materialization"] == "failed"
            assert payload["error"] == "Unknown primitive: pflair"
            assert payload["diagnostics"]["code"] == "E_RUNTIME_INSPECTION"
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_page_returns_json_400_for_vox_value_path_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    fake_storage = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    monkeypatch.setattr(main_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    fake_storage.put_success("node-map", {"a": 1})

    async def _fake_value_endpoint(_request):  # noqa: ANN001
        return {
            "available": True,
            "node_id": "node-map",
            "path": "/0",
            "materialization": "cached",
            "compute_status": "cached",
            "descriptor": {
                "vox_type": "mapping",
                "format_version": "voxpod/1",
                "summary": {"length": 1},
                "navigation": {
                    "path": "/0",
                    "pageable": True,
                    "can_descend": True,
                    "default_page_size": 64,
                    "max_page_size": 512,
                },
            },
        }

    monkeypatch.setattr(main_mod, "playground_value_endpoint", _fake_value_endpoint)

    try:
        with TestClient(main_mod.api_app) as client:
            page_resp = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = 1",
                    "variable": "xs",
                    "path": "/0",
                    "offset": 0,
                    "limit": 4,
                    "enqueue": False,
                },
            )
            assert page_resp.status_code == 400
            payload = page_resp.json()
            assert isinstance(payload, dict)
            assert "detail" in payload
            assert "Missing key" in str(payload["detail"])
    finally:
        fake_storage.close()


@pytest.mark.unit
def test_playground_value_exposes_sequence_navigation_while_job_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
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
                "job_id": "running-seq-job",
                "status": "running",
                "log_tail": "",
            },
            ensure_value_job=lambda payload, **kwargs: {"job_id": "unused", "status": "running", "log_tail": ""},
        ),
    )

    try:
        with TestClient(main_mod.api_app) as client:
            symbols = client.post("/api/v1/playground/symbols", json={"program": "xs = range(0,5)"})
            assert symbols.status_code == 200
            node_id = symbols.json()["symbol_table"]["xs"]

            running_value = client.post(
                "/api/v1/playground/value",
                json={"program": "xs = range(0,5)", "variable": "xs", "enqueue": False},
            )
            assert running_value.status_code == 200
            running_payload = running_value.json()
            assert running_payload["compute_status"] == "running"
            assert running_payload["descriptor"]["vox_type"] == "sequence"

            fake_storage.put_success(hash_sequence_item(node_id, 0), 101)
            running_page = client.post(
                "/api/v1/playground/value/page",
                json={
                    "program": "xs = range(0,5)",
                    "variable": "xs",
                    "offset": 0,
                    "limit": 4,
                    "enqueue": False,
                },
            )
            assert running_page.status_code == 200
            page_payload = running_page.json()
            assert page_payload["compute_status"] == "running"
            assert page_payload["descriptor"]["vox_type"] == "sequence"
            assert len(page_payload["page"]["items"]) == 4
            assert page_payload["page"]["items"][0]["path"] == "/0"
            assert page_payload["page"]["items"][0]["status"] == "materialized"
            assert page_payload["page"]["items"][1]["status"] == "pending"
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
def test_playground_symbols_reports_static_type_hints(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    with TestClient(main_mod.api_app) as client:
        resp = client.post(
            "/api/v1/playground/symbols",
            json={"program": "\n".join(["k = 10", "xs = range(0, 2)", "ov = overlay(k, k)"])},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["available"] is True
        assert payload["symbol_output_kinds"]["k"] in {"integer", "number"}
        assert payload["symbol_output_kinds"]["xs"] == "sequence"
        assert payload["symbol_output_kinds"]["ov"] == "overlay"


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


@pytest.mark.unit
def test_playground_value_websocket_supports_page_subscriptions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    requests: list[dict[str, object]] = []
    payloads = [
        {
            "materialization": "pending",
            "compute_status": "running",
            "page": {
                "offset": 0,
                "limit": 8,
                "has_more": True,
                "next_offset": 8,
                "items": [
                    {
                        "index": 0,
                        "label": "[0]",
                        "path": "/0",
                        "node_id": "child-0",
                        "state": "queued",
                        "status": "queued",
                        "descriptor": {"vox_type": "unavailable", "summary": {}},
                    }
                ],
            },
        },
        {
            "materialization": "computed",
            "compute_status": "completed",
            "page": {
                "offset": 0,
                "limit": 8,
                "has_more": False,
                "next_offset": None,
                "items": [
                    {
                        "index": 0,
                        "label": "[0]",
                        "path": "/0",
                        "node_id": "child-0",
                        "state": "ready",
                        "status": "ready",
                        "descriptor": {"vox_type": "integer", "summary": {"value": 7}},
                    }
                ],
            },
        },
    ]

    async def fake_page_endpoint(request):  # noqa: ANN001
        requests.append(
            {
                "variable": request.variable,
                "path": request.path,
                "offset": request.offset,
                "limit": request.limit,
                "enqueue": request.enqueue,
            }
        )
        return payloads.pop(0)

    monkeypatch.setattr(main_mod, "playground_value_page_endpoint", fake_page_endpoint)

    with TestClient(main_mod.api_app) as client:
        with client.websocket_connect("/ws/playground/value") as ws:
            ws.send_json(
                {
                    "type": "subscribe",
                    "mode": "page",
                    "request": {
                        "program": "xs = range(0, 2)",
                        "variable": "xs",
                        "path": "/",
                        "offset": 0,
                        "limit": 8,
                        "enqueue": True,
                    },
                }
            )

            subscribed = ws.receive_json()
            assert subscribed == {"type": "subscribed", "mode": "page"}

            first = ws.receive_json()
            assert first["type"] == "page"
            assert first["payload"]["page"]["items"][0]["state"] == "queued"

            second = ws.receive_json()
            assert second["type"] == "page"
            assert second["payload"]["page"]["items"][0]["state"] == "ready"

            terminal = ws.receive_json()
            assert terminal["type"] == "terminal"
            assert terminal["mode"] == "page"

    assert requests[0] == {
        "variable": "xs",
        "path": "/",
        "offset": 0,
        "limit": 8,
        "enqueue": True,
    }
    assert requests[1] == {
        "variable": "xs",
        "path": "/",
        "offset": 0,
        "limit": 8,
        "enqueue": False,
    }
