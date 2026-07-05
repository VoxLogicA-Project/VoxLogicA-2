"""Non-blocking, off-thread persistence of the engine's completed values.

Caching must never stall computation. Completed values are handed to a single
IO-bound writer thread through an unbounded queue, so submitting never blocks the
event loop, and serialization + disk writes happen entirely off the scheduling
thread. Each value is held only until it is written, then its reference is
dropped so it becomes collectible the moment the live tier is also done with it —
persistence never pins memory past the disk write.

The one thing an unbounded queue cannot do by itself is bound the *in-flight*
backlog when the disk is slower than compute. Rather than block the event loop
(which would stall all scheduling), the writer reports how many bytes are still
pending; the engine reads that to throttle the dispatch of *new* kernels — the
loop keeps running, memory stays bounded, and no cache entry is dropped.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from voxlogica.lazy.ir import NodeId
from voxlogica.storage import StorageBackend

logger = logging.getLogger(__name__)


def approx_bytes(value: object) -> int:
    """Approximate the resident size of one value (images dominate; rest is noise)."""
    pixels = getattr(value, "GetNumberOfPixels", None)
    if pixels is not None:  # SimpleITK image (duck-typed)
        try:
            return pixels() * value.GetNumberOfComponentsPerPixel() * 4
        except Exception:  # noqa: BLE001
            return 4_000_000
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    nbytes = getattr(value, "nbytes", None)
    if isinstance(nbytes, int):
        return nbytes
    if isinstance(value, (list, tuple)):
        return 64 + sum(approx_bytes(item) for item in value)
    return 64


class AsyncPersister:
    """Writes completed values to a backend on one IO thread, never blocking submit."""

    def __init__(self, backend: StorageBackend, max_pending_bytes: int):
        self._backend = backend
        self._max_pending_bytes = max_pending_bytes
        self._queue: "queue.SimpleQueue[tuple[NodeId, Any, dict, int] | None]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._pending_bytes = 0
        self._drained = threading.Event()
        self._drained.set()
        self._thread = threading.Thread(target=self._run, name="voxlogica-persist", daemon=True)
        self._thread.start()

    def submit(self, node_id: NodeId, value: Any, metadata: dict) -> None:
        """Hand a value to the writer thread. Never blocks."""
        size = approx_bytes(value)
        with self._lock:
            self._pending_bytes += size
            self._drained.clear()
        self._queue.put((node_id, value, metadata, size))

    @property
    def over_budget(self) -> bool:
        """True while the unwritten backlog exceeds the in-flight budget."""
        return self._pending_bytes > self._max_pending_bytes

    def flush(self, timeout_s: float = 30.0) -> None:
        """Block the caller (not the event loop) until the queue is fully written."""
        self._drained.wait(timeout_s)

    def close(self) -> None:
        """Stop the writer thread after it drains."""
        self._queue.put(None)
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            node_id, value, metadata, size = item
            try:
                # Idempotent: skip a value already durable on disk, so re-runs
                # over a warm cache do not rewrite unchanged payloads.
                if not self._backend.has(node_id):
                    self._backend.put_success(node_id, value, metadata=metadata)
            except Exception:  # noqa: BLE001
                logger.exception("async persistence failed for node %s", node_id)
            finally:
                value = item = None  # drop the reference: collectible once evicted
                with self._lock:
                    self._pending_bytes -= size
                    if self._pending_bytes <= 0:
                        self._pending_bytes = 0
                        self._drained.set()
