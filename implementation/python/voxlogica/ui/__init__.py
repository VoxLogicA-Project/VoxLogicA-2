"""The VoxLogicA browser UI: bundler, event hub, watcher, and HTTP server.

Entry point is :func:`start_ui`. The lifecycle it implements is:

* ``voxlogica run program.imgql`` brings the UI up alongside the computation.
  When the computation ends, the process exits **if nobody is watching** --
  otherwise it keeps serving until the last browser goes away, so a run you
  were looking at does not vanish the instant it finishes.
* ``voxlogica serve`` brings the same UI up with no computation and no
  auto-exit; it stops on Ctrl-C.

Several instances coexist. Each takes the next free port from 10001 and prints
its URL, and they share the content-addressed results store: two related
programs run from two terminals reuse every sub-expression they have in common,
which is the whole point of running them against one cache.
"""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

from .bundler import Bundle, BundleError, Bundler
from .hub import Hub
from .server import DEFAULT_PORT, UIServer, bind_loopback
from .watcher import UIWatcher
from .registration import announce, register_clients, withdraw
from .results import Results
from .workspace import Workspace

__all__ = [
    "Bundle", "BundleError", "Bundler", "Hub", "UIServer", "UIWatcher", "UISession",
    "Workspace", "Results", "DEFAULT_PORT", "start_ui",
]

logger = logging.getLogger(__name__)


class _StoreHandle:
    """The results store, opened when something first asks it a question.

    `voxlogica serve` is "a UI for the store alone" and has no computation to
    hand it one, so the UI opens its own. Lazily, because a workspace nobody has
    put a result card on never needs it, and a UI must start on a machine whose
    cache is missing or unreadable -- a database is a source of answers here, not
    a precondition.

    A second handle onto the same file is exactly what this store is built for:
    it is WAL, and concurrent runs sharing one cache is the reason it exists.
    """

    def __init__(self, db_path: str | None) -> None:
        self._db_path = db_path
        self._store = None
        self._tried = False

    def _open(self):
        if self._tried:
            return self._store
        self._tried = True
        from voxlogica.storage import SQLiteResultsDatabase, get_storage

        # A run in this process has already opened one; two handles would be two
        # caches' worth of memory budget for one cache.
        existing = get_storage()
        if existing is not None:
            self._store = existing
            return self._store
        try:
            self._store = SQLiteResultsDatabase(db_path=self._db_path)
        except Exception as exc:
            logger.debug("no results store for the UI (%s)", exc)
        return self._store

    def has(self, node_id: str) -> bool:
        store = self._open()
        return bool(store is not None and store.has(node_id))

    def value(self, node_id: str):
        store = self._open()
        if store is None:
            return None
        record = store.get_record(node_id)
        return None if record is None else record.value


class UISession:
    """A running UI: the server, the hub it publishes to, and its watcher."""

    def __init__(self, server: UIServer, hub: Hub, bundler: Bundler,
                 watcher: UIWatcher | None, workspace: Workspace | None = None,
                 record: Path | None = None, results: Results | None = None) -> None:
        self.server = server
        self.hub = hub
        self.bundler = bundler
        self.watcher = watcher
        self.workspace = workspace
        #: Node states. `results.observe` is what a run hands the engine.
        self.results = results
        self._record = record

    @property
    def url(self) -> str:
        return self.server.url

    @property
    def dev_url(self) -> str | None:
        """Deep link to the dev page, or ``None`` when serving a wheel.

        The page does not exist in a production bundle, so there is nothing to
        link to there. In a checkout the CLI prints this next to the app URL:
        a design system nobody can find is a design system nobody follows.
        """
        return f"{self.server.url}#dev" if self.bundler.is_dev else None

    def publish(self, event: dict, *, sticky_key: str | None = None) -> None:
        self.hub.publish(event, sticky_key=sticky_key)

    def client_count(self) -> int:
        return self.hub.client_count()

    def serve_until_idle(self) -> None:
        """Block while anyone is watching, then shut down.

        Returns at once when no client is connected -- which is the common case
        for a batch run, so a scripted ``voxlogica run`` costs one bound port
        and nothing else.
        """
        try:
            if self.hub.client_count() > 0:
                logger.info("UI still has %d client(s); serving at %s until they disconnect",
                            self.hub.client_count(), self.url)
                self.hub.wait_until_empty()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def serve_until_closed(self, *, patience: float = 30.0) -> None:
        """Wait for the window, then serve until it goes away.

        An application ends when its window does. The wait is bounded so a
        browser that never arrives -- blocked, misconfigured, opened on the wrong
        machine -- leaves a process that exits rather than one that lingers.
        """
        import time

        try:
            deadline = time.monotonic() + patience
            while self.hub.client_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.1)
            if self.hub.client_count() == 0:
                logger.info("no window connected within %.0fs; stopping", patience)
                return
            self.hub.wait_until_empty()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def serve_forever(self) -> None:
        """Block until interrupted. Used by ``voxlogica serve``."""
        import time
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        withdraw(self._record)
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        self.server.stop()


def start_ui(
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    instance_info: dict | None = None,
    dev: bool | None = None,
    source_root: Path | None = None,
    program: Path | None = None,
    workspace_path: Path | None = None,
    store_db: str | None = None,
) -> UISession:
    """Bring the UI up on the first free port at or after ``port``.

    The bundle is *not* built here: it is built on the first page load. A run
    that nobody opens therefore never invokes node, which keeps the UI off the
    critical path of a batch run.
    """
    bundler = Bundler(source_root=source_root, dev=True if dev is None else dev)
    hub = Hub()
    # The workspace exists before any browser does: an agent on the MCP server is
    # a first-class client, and "what is on the board" cannot depend on whether
    # somebody happens to have a tab open.
    # `program` is what a run computes; `workspace_path` is the document being
    # edited. For `voxlogica run` they are the same file, which is the point of
    # a workspace that is a program.
    # Node states, and the results store as their second source: a hash whose
    # value is already cached is `done` before anything runs, which is the most
    # useful thing this system does and would be a lie to render as `unknown`.
    # Asked lazily and defensively -- a UI must not be what fails when a cache
    # is unreadable.
    store = _StoreHandle(store_db)
    results = Results(hub, probe=store.has, fetch=store.value)
    workspace = Workspace(hub=hub, path=workspace_path or program, results=results)
    sock = bind_loopback(port)
    server = UIServer(
        hub=hub, bundler=bundler, sock=sock, instance_info=instance_info,
        workspace=workspace, results=results,
    )
    server.start()
    workspace.publish()

    # Findable without anyone editing JSON: the instance announces itself, and
    # every installed MCP client gains a `voxlogica` entry if it has none. Both
    # are best-effort by construction -- see registration.py.
    session_record = announce(port=server.port, url=server.url, program=str(program or "") or None)
    registered = register_clients()
    if registered:
        logger.info("registered the VoxLogicA MCP server with: %s", ", ".join(registered))

    watcher: UIWatcher | None = None
    if bundler.is_dev:
        candidate = UIWatcher(bundler, hub)
        watcher = candidate if candidate.start() else None

    if open_browser:
        webbrowser.open(server.url)
    return UISession(server, hub, bundler, watcher, workspace, session_record, results)
