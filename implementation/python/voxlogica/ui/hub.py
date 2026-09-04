"""Who is watching, and what they are told.

The hub answers one question the CLI needs at the end of a run -- *is anybody
looking at this?* -- and one the browser needs continuously -- *what just
happened?*

Presence is a WebSocket connection plus a heartbeat. The connection alone is
not enough: a laptop that suspends, or a network that drops without a FIN,
leaves a socket that looks open for minutes. So every client pings on an
interval and a client that has not pinged within :data:`CLIENT_TTL` is
considered gone. The cost of getting this wrong is asymmetric -- a false
"nobody is here" kills a UI somebody is using, a false "somebody is here" just
keeps a process alive a few seconds longer -- so the TTL is several times the
ping interval.

The hub is shared between the CLI thread (which asks about presence) and the
server's event loop (which serves WebSockets), so its state is under a plain
lock and events reach subscribers via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: How often the browser is asked to ping.
PING_INTERVAL = 5.0
#: A client silent for longer than this is treated as gone.
#:
#: Far above the ping interval on purpose. Browsers throttle timers in hidden
#: pages -- Chrome drops setInterval in a tab that has been in the background
#: for a few minutes to roughly once a minute -- so a UI you left open in
#: another tab keeps pinging, just slowly. A TTL near the ping interval would
#: read that as "nobody is watching" and let the run exit, killing the very
#: session this rule exists to preserve. The errors are asymmetric: a late
#: eviction costs a few idle seconds, an early one costs the user their UI.
CLIENT_TTL = 120.0


class Hub:
    """Client presence plus a fan-out event channel."""

    def __init__(self, *, client_ttl: float = CLIENT_TTL) -> None:
        self._ttl = client_ttl
        self._lock = threading.Lock()
        self._clients: dict[str, float] = {}
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._ids = itertools.count(1)
        self._departed = threading.Event()
        self._departed.set()  # nobody has ever connected yet
        #: Last event of each "sticky" kind, replayed to a client on connect so
        #: a browser opened mid-run is not staring at an empty screen.
        self._sticky: dict[str, dict[str, Any]] = {}

    # -------------------------------------------------------------- presence

    def connect(self) -> str:
        with self._lock:
            client_id = f"c{next(self._ids)}"
            self._clients[client_id] = time.monotonic()
            self._departed.clear()
        logger.debug("UI client connected: %s", client_id)
        return client_id

    def heartbeat(self, client_id: str) -> None:
        with self._lock:
            if client_id in self._clients:
                self._clients[client_id] = time.monotonic()

    def disconnect(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)
            if not self._clients:
                self._departed.set()
        logger.debug("UI client disconnected: %s", client_id)

    def client_count(self) -> int:
        """Live clients, after evicting the ones that stopped heartbeating."""
        with self._lock:
            self._prune_locked()
            return len(self._clients)

    def _prune_locked(self) -> None:
        deadline = time.monotonic() - self._ttl
        stale = [cid for cid, seen in self._clients.items() if seen < deadline]
        for cid in stale:
            logger.debug("UI client timed out: %s", cid)
            del self._clients[cid]
        if not self._clients:
            self._departed.set()

    def wait_until_empty(self, *, poll: float = 1.0, timeout: float | None = None) -> bool:
        """Block until no client is connected. Returns immediately if none is.

        ``poll`` bounds how long a timed-out (as opposed to cleanly
        disconnected) client can keep the process alive.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.client_count() == 0:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            remaining = poll if deadline is None else min(poll, deadline - time.monotonic())
            self._departed.wait(max(remaining, 0.0))

    # ---------------------------------------------------------------- events

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        with self._lock:
            self._subscribers.append((loop, queue))
            backlog = list(self._sticky.values())
        for event in backlog:
            queue.put_nowait(event)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(l, q) for (l, q) in self._subscribers if q is not queue]

    def clear_sticky(self, sticky_key: str) -> None:
        """Forget a replayed event, so a newly connected client is not told about
        a condition that has since been resolved."""
        with self._lock:
            self._sticky.pop(sticky_key, None)

    def publish(self, event: dict[str, Any], *, sticky_key: str | None = None) -> None:
        """Fan an event out to every connected client. Safe from any thread."""
        with self._lock:
            if sticky_key is not None:
                self._sticky[sticky_key] = event
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, event)
            except RuntimeError:
                # The loop is closing; its connections are on their way out.
                continue

    @staticmethod
    def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # A client too slow to drain its queue gets dropped events rather
            # than backpressuring the engine that is publishing them.
            logger.debug("dropping UI event for a slow client")
