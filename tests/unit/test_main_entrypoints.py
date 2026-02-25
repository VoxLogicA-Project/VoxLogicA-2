from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import typer

from voxlogica.features import OperationResult
from voxlogica import main as main_mod


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
def test_api_endpoints_and_root(monkeypatch: pytest.MonkeyPatch):
    class FakeRegistry:
        @staticmethod
        def get_feature(name: str):
            if name == "version":
                return SimpleNamespace(handler=lambda: OperationResult.ok({"version": "2.0.0"}))
            if name == "run":
                return SimpleNamespace(handler=lambda **kwargs: OperationResult.ok({"ok": True, "args": kwargs}))
            return None

    monkeypatch.setattr(main_mod, "FeatureRegistry", FakeRegistry)
    monkeypatch.setattr(main_mod, "handle_list_primitives", lambda namespace=None: OperationResult.ok({"n": namespace}))
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

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

        root_resp = client.get("/")
        assert root_resp.status_code == 200


@pytest.mark.unit
def test_api_error_paths(monkeypatch: pytest.MonkeyPatch):
    class MissingRegistry:
        @staticmethod
        def get_feature(_name: str):
            return None

    monkeypatch.setattr(main_mod, "FeatureRegistry", MissingRegistry)
    monkeypatch.setattr(main_mod, "start_file_watcher", lambda: None)
    monkeypatch.setattr(main_mod, "stop_file_watcher", lambda: None)

    with TestClient(main_mod.api_app) as client:
        assert client.get("/api/v1/version").status_code == 404
        assert client.post("/api/v1/run", json={"program": "x"}).status_code == 404

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

