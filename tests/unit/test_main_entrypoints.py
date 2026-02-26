from __future__ import annotations

import asyncio
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

    main_mod.run(str(src), execute=False, execution_strategy="strict")
    assert captured["filename"] == str(src)
    assert captured["execute"] is False
    assert captured["execution_strategy"] == "strict"

    captured.clear()
    main_mod.run(str(src), execute=False, execution_strategy="dask", strict=True)
    assert captured["execution_strategy"] == "strict"

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

    monkeypatch.setattr(main_mod, "run_interactive_repl", lambda strategy: 7)
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
    monkeypatch.setattr(
        main_mod,
        "playground_jobs",
        SimpleNamespace(
            create_job=lambda payload: {"job_id": "p1", "status": "running"},
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

            symbols_resp = client.post("/api/v1/playground/symbols", json={"program": "let x = 2 + 3"})
            assert symbols_resp.status_code == 200
            symbol_payload = symbols_resp.json()
            assert symbol_payload["available"] is True
            assert "x" in symbol_payload["symbol_table"]
            node_id = symbol_payload["symbol_table"]["x"]

            value_pending = client.post(
                "/api/v1/playground/value",
                json={"program": "let x = 2 + 3", "variable": "x", "execution_strategy": "strict"},
            )
            assert value_pending.status_code == 200
            assert value_pending.json()["materialization"] == "pending"
            assert value_pending.json()["compute_status"] in {"queued", "running"}

            # Variable lookup must win over stale/foreign node ids.
            value_with_stale_node = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "let x = 2 + 3",
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
                    "program": "let x = 2 + 3",
                    "variable": "x",
                    "execution_strategy": "strict",
                    "enqueue": False,
                },
            )
            assert value_cached.status_code == 200
            assert value_cached.json()["materialization"] == "cached"
            assert value_cached.json()["descriptor"]["kind"] == "integer"
            assert value_cached.json()["descriptor"]["value"] == 5

            invalid_node = client.post(
                "/api/v1/playground/value",
                json={
                    "program": "let x = 2 + 3",
                    "node_id": "deadbeef",
                    "execution_strategy": "strict",
                    "enqueue": False,
                },
            )
            assert invalid_node.status_code == 400

            jobs_resp = client.get("/api/v1/playground/jobs")
            assert jobs_resp.status_code == 200
            assert "jobs" in jobs_resp.json()
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
    finally:
        fake_storage.close()


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
