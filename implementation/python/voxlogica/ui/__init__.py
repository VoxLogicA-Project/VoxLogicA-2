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

__all__ = [
    "Bundle", "BundleError", "Bundler", "Hub", "UIServer", "UIWatcher", "UISession",
    "DEFAULT_PORT", "start_ui",
]

logger = logging.getLogger(__name__)


class UISession:
    """A running UI: the server, the hub it publishes to, and its watcher."""

    def __init__(self, server: UIServer, hub: Hub, bundler: Bundler,
                 watcher: UIWatcher | None) -> None:
        self.server = server
        self.hub = hub
        self.bundler = bundler
        self.watcher = watcher

    @property
    def url(self) -> str:
        return self.server.url

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
) -> UISession:
    """Bring the UI up on the first free port at or after ``port``.

    The bundle is *not* built here: it is built on the first page load. A run
    that nobody opens therefore never invokes node, which keeps the UI off the
    critical path of a batch run.
    """
    bundler = Bundler(source_root=source_root, dev=True if dev is None else dev)
    hub = Hub()
    sock = bind_loopback(port)
    server = UIServer(hub=hub, bundler=bundler, sock=sock, instance_info=instance_info)
    server.start()

    watcher: UIWatcher | None = None
    if bundler.is_dev:
        candidate = UIWatcher(bundler, hub)
        watcher = candidate if candidate.start() else None

    if open_browser:
        webbrowser.open(server.url)
    return UISession(server, hub, bundler, watcher)
