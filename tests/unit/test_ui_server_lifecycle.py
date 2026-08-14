"""The UI must not outlive its usefulness, nor die while someone is watching.

Three properties hold this together, and each has a failure mode that is
invisible until it bites:

* **Presence is heartbeat-based, not socket-based.** A suspended laptop leaves
  a TCP connection that looks open for minutes; if that counted as "somebody is
  watching", every batch run would hang at the end waiting for a ghost.
* **Ports are bound, not guessed.** Concurrent runs on one machine share a
  results store on purpose, so several of them coexist; each must land on its
  own port without a race.
* **The bundle fingerprint tracks the sources and nothing else.** It is what a
  page load and the watcher both consult, so if it moved for unrelated reasons
  (or failed to move for a real edit) the UI would either rebuild constantly or
  serve stale bytes forever.

None of this needs node: the bundler is only asked what it would build.
"""

from __future__ import annotations

import socket
import time

import pytest

from voxlogica.ui.bundler import Bundler, BundleError
from voxlogica.ui.hub import Hub
from voxlogica.ui.server import bind_loopback

pytestmark = pytest.mark.unit


def test_a_client_that_stops_heartbeating_is_dropped() -> None:
    hub = Hub(client_ttl=0.2)
    client = hub.connect()
    assert hub.client_count() == 1

    hub.heartbeat(client)
    assert hub.client_count() == 1

    time.sleep(0.3)
    assert hub.client_count() == 0


def test_wait_until_empty_returns_at_once_with_no_clients() -> None:
    hub = Hub()
    started = time.monotonic()
    assert hub.wait_until_empty() is True
    assert time.monotonic() - started < 0.5


def test_wait_until_empty_unblocks_on_disconnect() -> None:
    hub = Hub()
    client = hub.connect()
    assert hub.wait_until_empty(timeout=0.2) is False
    hub.disconnect(client)
    assert hub.wait_until_empty(timeout=1.0) is True


def test_sticky_events_reach_a_client_that_connects_later() -> None:
    import asyncio

    hub = Hub()

    async def scenario() -> list:
        hub.publish({"type": "run", "run": {"status": "running"}}, sticky_key="run")
        queue = hub.subscribe(asyncio.get_running_loop())
        first = queue.get_nowait()

        hub.clear_sticky("run")
        later = hub.subscribe(asyncio.get_running_loop())
        return [first, later.empty()]

    replayed, cleared_is_empty = asyncio.run(scenario())
    assert replayed["run"]["status"] == "running"
    assert cleared_is_empty is True


def test_concurrent_instances_take_consecutive_ports() -> None:
    first = bind_loopback(10001)
    try:
        second = bind_loopback(10001)
        try:
            assert second.getsockname()[1] > first.getsockname()[1]
        finally:
            second.close()
    finally:
        first.close()


def test_binding_fails_loudly_when_the_whole_range_is_taken() -> None:
    held = [bind_loopback(10001, attempts=1)]
    try:
        with pytest.raises(RuntimeError, match="No free port"):
            bind_loopback(held[0].getsockname()[1], attempts=1)
    finally:
        for sock in held:
            sock.close()


def test_a_run_keeps_serving_until_the_last_browser_leaves() -> None:
    """The rule ``run`` depends on: exit when nobody is watching, not before."""
    import threading

    from voxlogica.ui import UISession

    class _StubServer:
        url = "http://127.0.0.1:0/"

        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    server = _StubServer()
    hub = Hub()
    session = UISession(server, hub, Bundler(source_root=None), None)

    client = hub.connect()
    finished = threading.Event()
    threading.Thread(target=lambda: (session.serve_until_idle(), finished.set()),
                     daemon=True).start()

    assert not finished.wait(0.5), "a connected browser must hold the process open"
    assert not server.stopped

    hub.disconnect(client)
    assert finished.wait(5.0), "the process must exit once the last browser leaves"
    assert server.stopped


def _ui_tree(root):
    (root / "src" / "lib").mkdir(parents=True)
    (root / "build.mjs").write_text("// build\n")
    (root / "package.json").write_text("{}\n")
    (root / "index.html").write_text("<html></html>\n")
    (root / "src" / "main.js").write_text("export const a = 1;\n")
    return root


def test_fingerprint_moves_only_for_real_source_edits(tmp_path) -> None:
    root = _ui_tree(tmp_path / "ui")
    bundler = Bundler(source_root=root)
    baseline = bundler.fingerprint()

    assert bundler.fingerprint() == baseline, "a pure re-read must not invalidate"

    # Build outputs and dependencies are not inputs: hashing node_modules would
    # dominate the walk and rebuild on every npm install.
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("x" * 1000)
    (root / "dist").mkdir()
    (root / "dist" / "app.js").write_text("stale")
    assert bundler.fingerprint() == baseline

    (root / "src" / "main.js").write_text("export const a = 2;\n")
    assert bundler.fingerprint() != baseline


def test_fingerprint_notices_a_new_file(tmp_path) -> None:
    root = _ui_tree(tmp_path / "ui")
    bundler = Bundler(source_root=root)
    baseline = bundler.fingerprint()
    (root / "src" / "lib" / "extra.js").write_text("export const b = 1;\n")
    assert bundler.fingerprint() != baseline


def test_an_install_without_sources_or_a_prebuilt_bundle_says_so(monkeypatch) -> None:
    from voxlogica.ui import bundler as bundler_module

    monkeypatch.setattr(bundler_module, "_source_root", lambda: None)
    monkeypatch.setattr(bundler_module, "_package_static", lambda: bundler_module.Path("/nonexistent"))
    with pytest.raises(BundleError, match="No UI bundle available"):
        Bundler().ensure()
