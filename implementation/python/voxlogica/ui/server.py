"""Lifecycle for the UI server: pick a port, run uvicorn off the main thread.

The server lives in a background thread of the same process that runs the
computation, on a loopback socket bound *before* uvicorn starts -- so port
selection is a bind rather than a guess, and two concurrent ``voxlogica run``
invocations cannot race for the same port. Nothing here is reachable from off
this machine.

The ASGI application itself is in :mod:`voxlogica.ui.app`, which is imported
lazily: the CLI imports this module only to read :data:`DEFAULT_PORT`, and must
not pay for fastapi to do it.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading

from . import guard
from .bundler import Bundler
from .hub import Hub

logger = logging.getLogger(__name__)

DEFAULT_PORT = 10001
#: How many consecutive ports to try. Each concurrent run on this host takes one.
PORT_ATTEMPTS = 32


def bind_loopback(preferred: int = DEFAULT_PORT, attempts: int = PORT_ATTEMPTS) -> socket.socket:
    """Bind the first free port at or after ``preferred``, on loopback only."""
    # Loopback, so the only client is whoever is at this machine, and the path
    # rule is correspondingly empty. The day this binds anything else, this call
    # is where the boundary comes back. See guard.py.
    guard.configure(local_only=True)
    last: OSError | None = None
    for port in range(preferred, preferred + attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            sock.close()
            last = exc
            continue
        sock.listen(64)
        # Left non-inheritable (the Python default): uvicorn is handed this
        # socket object directly and never needs it across an exec, whereas a
        # subprocess that inherited it -- an nnU-Net training that outlives the
        # run by hours -- would keep the port bound long after we exited.
        return sock
    raise RuntimeError(
        f"No free port in {preferred}..{preferred + attempts - 1} for the VoxLogicA UI"
    ) from last


class UIServer:
    """A threaded uvicorn hosting the bundle, the API, and the event socket."""

    def __init__(
        self,
        *,
        hub: Hub,
        bundler: Bundler,
        sock: socket.socket,
        instance_info: dict | None = None,
        workspace=None,
    ) -> None:
        self._hub = hub
        self._bundler = bundler
        self._sock = sock
        self._instance_info = dict(instance_info or {})
        self._workspace = workspace
        self._thread: threading.Thread | None = None
        self._server = None
        self._ready = threading.Event()
        self._failure: BaseException | None = None
        host, port = sock.getsockname()[:2]
        self.port = int(port)
        self.url = f"http://{host}:{self.port}/"

    def _describe(self) -> dict:
        payload = dict(self._instance_info)
        payload["port"] = self.port
        payload["url"] = self.url
        return payload

    # ------------------------------------------------------------- lifecycle

    def start(self, *, timeout: float = 15.0) -> str:
        import uvicorn

        from .app import build_app

        # h11 and the stdlib loop rather than uvloop/httptools: this is a
        # free-threaded interpreter, and the pure-Python path is the one whose
        # wheels exist for it. The UI serves a handful of requests; the C
        # accelerators would buy nothing measurable.
        config = uvicorn.Config(
            build_app(
                hub=self._hub,
                bundler=self._bundler,
                describe=self._describe,
                workspace=self._workspace,
            ),
            loop="asyncio",
            http="h11",
            ws="websockets",
            log_level="warning",
            access_log=False,
            lifespan="off",
        )

        class _Threaded(uvicorn.Server):
            # Signal handlers can only be installed on the main thread, and the
            # CLI owns them anyway: SIGINT must interrupt the computation, not
            # merely the web server.
            def install_signal_handlers(self) -> None:
                return None

        self._server = _Threaded(config)
        self._thread = threading.Thread(target=self._serve, name="voxlogica-ui", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("the VoxLogicA UI server did not start in time")
        if self._failure is not None:
            raise self._failure
        return self.url

    def _serve(self) -> None:
        assert self._server is not None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve_async())
        except BaseException as exc:  # noqa: BLE001 - reported to start()
            self._failure = exc
        finally:
            self._ready.set()
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)

    async def _serve_async(self) -> None:
        assert self._server is not None
        serve = asyncio.ensure_future(self._server.serve(sockets=[self._sock]))
        while not getattr(self._server, "started", False) and not serve.done():
            await asyncio.sleep(0.01)
        self._ready.set()
        await serve

    def stop(self, *, timeout: float = 5.0) -> None:
        # Whatever the autosave was still waiting to write, write it. Losing the
        # last half-second of somebody's arrangement because they closed the tab
        # is exactly the failure autosave exists to prevent.
        if self._workspace is not None:
            self._workspace.flush()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
