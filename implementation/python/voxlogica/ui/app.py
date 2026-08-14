"""The ASGI application: static bundle, instance API, event WebSocket.

Kept apart from :mod:`voxlogica.ui.server` so that fastapi is imported only when
a UI is actually being started -- ``server.py`` is imported by the CLI just for
its default port.

Note the deliberate absence of ``from __future__ import annotations`` here.
FastAPI resolves endpoint annotations at decoration time to decide what to
inject; under postponed evaluation an unresolvable name silently degrades to "a
query parameter of that name", which for a ``WebSocket`` argument means the
handshake is rejected with 403 and nothing anywhere says why. Real annotations
and module-level imports keep that failure impossible.
"""

import asyncio
import html
import logging
import os
from typing import Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .bundler import Bundle, BundleError, Bundler
from .hub import PING_INTERVAL, Hub
from .mcp import CaptureBroker, mount as mount_mcp, screenshot

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


def build_app(
    *, hub: Hub, bundler: Bundler, describe: Callable[[], dict], workspace=None
) -> FastAPI:
    app = FastAPI(title="VoxLogicA UI", docs_url=None, redoc_url=None, openapi_url=None)

    # Screenshots an agent asked for and a browser will answer. Held here because
    # the WebSocket that carries the answers is declared in this function.
    captures = CaptureBroker()
    if workspace is not None:
        mount_mcp(app, workspace, hub, captures)

    def bundle_or_error() -> tuple[Bundle | None, BundleError | None]:
        try:
            return bundler.ensure(), None
        except BundleError as exc:
            return None, exc

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        # to_thread: ensure() may stat the source tree and shell out to esbuild,
        # and neither belongs on the event loop that is serving WebSockets.
        bundle, error = await asyncio.to_thread(bundle_or_error)
        if error is not None:
            return HTMLResponse(error_page(error), headers={"Cache-Control": "no-store"})
        assert bundle is not None
        return HTMLResponse(bundle.index_html.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/instance")
    async def instance() -> JSONResponse:
        payload = describe()
        payload.update({
            "pid": os.getpid(),
            "dev": bundler.is_dev,
            "clients": hub.client_count(),
            "pingInterval": PING_INTERVAL,
        })
        return JSONResponse(payload)

    @app.get("/api/workspace")
    async def workspace_state() -> JSONResponse:
        # The same snapshot the WebSocket pushes, for anything that would rather
        # ask once than hold a connection open.
        if workspace is None:
            return JSONResponse({"workspace": None})
        return JSONResponse({"workspace": workspace.snapshot()})

    @app.post("/api/action")
    async def run_action(payload: dict) -> JSONResponse:
        """Apply an action by name. The `voxlogica mcp` bridge speaks this.

        The same dispatcher the WebSocket uses, over one request: an agent in
        another process should not have to hold a socket open to move a card.
        """
        if workspace is None:
            raise HTTPException(status_code=404, detail="no workspace")
        try:
            result = workspace.apply(payload.get("name"), payload.get("params") or {})
        except Exception as error:  # noqa: BLE001 - reported, not raised
            return JSONResponse({"ok": False, "error": f"{type(error).__name__}: {error}"})
        return JSONResponse({"ok": True, "result": result, "workspace": workspace.snapshot()})

    @app.post("/api/capture")
    async def capture(payload: dict) -> JSONResponse:
        if workspace is None:
            raise HTTPException(status_code=404, detail="no workspace")
        return JSONResponse(await screenshot(hub, captures, payload.get("target")))

    @app.get("/api/build")
    async def build_status() -> JSONResponse:
        bundle, error = await asyncio.to_thread(bundle_or_error)
        if error is not None:
            return JSONResponse({"ok": False, "summary": error.summary, "detail": error.detail})
        assert bundle is not None
        return JSONResponse({"ok": True, "fingerprint": bundle.fingerprint})

    @app.websocket("/ws")
    async def events(websocket: WebSocket) -> None:
        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue = hub.subscribe(loop)
        client_id = hub.connect()
        try:
            await websocket.send_json({
                "type": "hello", "clientId": client_id, "pingInterval": PING_INTERVAL,
            })

            async def pump() -> None:
                while True:
                    await websocket.send_json(await queue.get())

            async def receive() -> None:
                while True:
                    message = await websocket.receive_json()
                    if message.get("type") == "ping":
                        hub.heartbeat(client_id)
                        await websocket.send_json({"type": "pong"})
                    elif message.get("type") == "captureResult":
                        # This tab answering the screenshot an agent asked for.
                        captures.settle(message.get("id"), message)
                    elif message.get("type") == "action" and workspace is not None:
                        # The browser never mutates its own copy: it asks for an
                        # action by name and redraws from the state that comes
                        # back, which is the same state an agent sees.
                        reply: dict = {"type": "actionResult", "id": message.get("id")}
                        try:
                            reply["result"] = workspace.apply(
                                message["name"], message.get("params") or {}
                            )
                            reply["ok"] = True
                        except Exception as error:
                            reply["ok"] = False
                            reply["error"] = f"{type(error).__name__}: {error}"
                        await websocket.send_json(reply)

            tasks = [asyncio.create_task(pump()), asyncio.create_task(receive())]
            done: set = set()
            try:
                done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
            # Re-raise whichever side ended, so a genuine error is logged rather
            # than silently ending the connection.
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        except Exception:  # a malformed frame must not take the server down
            logger.debug("UI websocket ended with an error", exc_info=True)
        finally:
            hub.unsubscribe(queue)
            hub.disconnect(client_id)

    # Declared last on purpose: FastAPI matches in declaration order, and this
    # catch-all would otherwise swallow every /api path above.
    @app.get("/{asset:path}")
    async def asset(asset: str) -> Response:
        bundle, error = await asyncio.to_thread(bundle_or_error)
        if error is not None:
            raise HTTPException(status_code=503, detail=error.summary)
        assert bundle is not None
        root = bundle.directory.resolve()
        path = (root / asset).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404)
        media = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        return Response(path.read_bytes(), media_type=media,
                        headers={"Cache-Control": "no-store"})

    return app


def error_page(error: BundleError) -> str:
    """A standalone page for when the bundle does not compile.

    It cannot use the app's own WebSocket -- there is no app -- so it polls for
    a working build and reloads itself once one appears.
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>VoxLogicA - build failed</title>
<style>
  body {{ margin:0; padding:2rem; background:#1c1917; color:#fafaf9;
         font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; }}
  h1 {{ font-size:1rem; color:#fca5a5; margin:0 0 1rem; }}
  pre {{ white-space:pre-wrap; overflow-x:auto; }}
  p {{ color:#a8a29e; }}
</style></head><body>
<h1>{html.escape(error.summary)}</h1>
<pre>{html.escape(error.detail)}</pre>
<p>Waiting for the sources to compile&hellip; this page reloads by itself.</p>
<script>
  setInterval(async () => {{
    try {{
      const r = await fetch('/api/build', {{ cache: 'no-store' }});
      if ((await r.json()).ok) location.reload();
    }} catch (e) {{ /* server going away; keep trying */ }}
  }}, 1000);
</script>
</body></html>"""
