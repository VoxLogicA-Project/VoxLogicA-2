"""Rebuild the UI when its own sources change. Development only.

Scope is deliberately narrow, and the narrowness is the point:

* It watches **only the UI source tree** (``implementation/ui``). Nothing under
  ``implementation/python`` is observed, so editing engine code never triggers
  a rebuild, never reloads a browser, and above all never restarts the serving
  process -- the run in flight is not something a text editor gets to
  interrupt. There is no uvicorn ``--reload`` anywhere in this package.
* It only ever *invalidates and rebuilds*. It cannot restart, reconfigure, or
  shut anything down.
* It exists only when a source tree exists. A wheel serving its prebuilt bundle
  starts no watcher at all.

It polls rather than subscribing to filesystem events, which is a deliberate
trade. watchdog is the obvious choice and is already a dependency, but on macOS
it loads ``_watchdog_fsevents``, a C extension that has not declared
free-threaded support -- importing it makes CPython **re-enable the GIL** for
the whole process. Handing back the engine's parallelism to get a live-reload
notification a fraction of a second sooner is not a trade worth making.

The poll is the same stat walk the bundler already fingerprints with: a few
dozen ``stat()`` calls, well under a millisecond, a few times a second. It also
collapses two mechanisms into one -- what the watcher notices and what a page
load notices are by construction the same thing, so they cannot disagree.
"""

from __future__ import annotations

import logging
import threading

from .bundler import BundleError, Bundler
from .hub import Hub

logger = logging.getLogger(__name__)

#: How often the source tree is fingerprinted.
POLL_INTERVAL = 0.4
#: Quiet period required before rebuilding. Editors write a file several times
#: per save (temp file, rename, metadata); building on the first write compiles
#: a half-saved tree and reloads the browser into it.
SETTLE = 0.25


class UIWatcher:
    """Polls the UI sources and pushes a reload once a rebuild lands."""

    def __init__(self, bundler: Bundler, hub: Hub, *, poll: float = POLL_INTERVAL,
                 settle: float = SETTLE) -> None:
        self._bundler = bundler
        self._hub = hub
        self._poll = poll
        self._settle = settle
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        if self._bundler.source_root is None:
            return False
        self._thread = threading.Thread(target=self._loop, name="voxlogica-ui-watch",
                                        daemon=True)
        self._thread.start()
        logger.info("Watching UI sources at %s", self._bundler.source_root)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        last = self._bundler.fingerprint()
        while not self._stop.wait(self._poll):
            current = self._bundler.fingerprint()
            if current == last:
                continue
            # Wait for the tree to stop moving before compiling it.
            while not self._stop.wait(self._settle):
                settled = self._bundler.fingerprint()
                if settled == current:
                    break
                current = settled
            if self._stop.is_set():
                return
            last = current
            self._rebuild()

    def _rebuild(self) -> None:
        # A previous failure is memoised against its fingerprint; an edit means
        # the rebuild should really run.
        self._bundler.invalidate()
        try:
            bundle = self._bundler.ensure()
        except BundleError as exc:
            self._hub.publish(
                {"type": "ui-build-error", "summary": exc.summary, "detail": exc.detail},
                sticky_key="build",
            )
            logger.warning("UI build failed: %s", exc.summary)
            return
        # Not sticky: a reload replayed to every newly connected client would
        # reload it, reconnect it, and replay again -- a loop.
        self._hub.clear_sticky("build")
        self._hub.publish({"type": "ui-reload", "fingerprint": bundle.fingerprint})
        logger.info("UI rebuilt (%s)", bundle.fingerprint)
