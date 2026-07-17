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

    def __init__(self, backend: StorageBackend, max_pending_bytes: int,
                 persisted_ids: set[NodeId] | None = None):
        self._backend = backend
        self._max_pending_bytes = max_pending_bytes
        # Shared with NodeTable: consulted to skip a redundant disk probe per
        # write, appended after each successful write so ``persisted()`` sees
        # this run's results without touching SQLite. Single set ops from this
        # thread are GIL-atomic; no lock needed.
        self._persisted_ids = persisted_ids
        self._queue: "queue.SimpleQueue[tuple[NodeId, Any, dict, int, float] | None]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._pending_bytes = 0
        self._drained = threading.Event()
        self._drained.set()
        # Several writer threads so persistence throughput keeps up with compute:
        # gzip (the costly part) and the payload-file write happen outside the
        # backend's write lock, so N writers compress in parallel and serialise
        # only the short SQLite insert. Without this a single writer fell behind a
        # wide sweep and best-effort dropped the very results worth caching.
        import os
        self._num_writers = int(os.environ.get("VOXLOGICA_PERSIST_WRITERS", 0)) or min(4, (os.cpu_count() or 4))
        self._threads = [
            threading.Thread(target=self._run, name=f"voxlogica-persist-{i}", daemon=True)
            for i in range(self._num_writers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, node_id: NodeId, value: Any, metadata: dict, compute_ms: float = 0.0,
               size: int | None = None) -> None:
        """Hand a value to the writer thread. Never blocks.

        ``size`` lets the caller pass an already-computed ``approx_bytes`` so
        the (recursive, per-completion) measurement is not repeated on the
        event loop.
        """
        if size is None:
            size = approx_bytes(value)
        with self._lock:
            self._pending_bytes += size
            self._drained.clear()
        self._queue.put((node_id, value, metadata, size, compute_ms))

    @property
    def over_budget(self) -> bool:
        """True while the unwritten backlog exceeds the in-flight budget."""
        return self._pending_bytes > self._max_pending_bytes

    @property
    def pending_bytes(self) -> int:
        """Bytes of values queued for writing but not yet persisted.

        These objects are resident in RAM (the queue holds a reference) yet are
        NOT in the live tier's ``live_bytes`` — a value evicted from the live
        tier stays alive here until written. The admission controller adds this
        to the live tier to get the true resident total, so a slow disk applies
        real backpressure instead of letting the backlog grow until OOM. Plain
        int read; GIL-atomic, no lock needed.
        """
        return self._pending_bytes

    def flush(self, timeout_s: float = 30.0) -> None:
        """Block the caller (not the event loop) until the queue is fully written."""
        self._drained.wait(timeout_s)

    def close(self) -> None:
        """Stop the writer threads after they drain."""
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=2.0)

    # Rows committed per transaction. Without batching every row pays its own
    # WAL commit; at frontier-scheduler dispatch rates that made the writer
    # fsync-bound and the queue drained slower than compute filled it.
    _BATCH = 64

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            # Opportunistically drain more work into the same transaction. A
            # sentinel drained by mistake is put back for its intended thread.
            batch = [item]
            while len(batch) < self._BATCH:
                try:
                    extra = self._queue.get_nowait()
                except queue.Empty:
                    break
                if extra is None:
                    self._queue.put(None)
                    break
                batch.append(extra)
            self._write_batch(batch)

    def _write_batch(self, batch: list[tuple[NodeId, Any, dict, int, float]]) -> None:
        try:
            # Idempotent: skip values already durable on disk, so re-runs over
            # a warm cache do not rewrite unchanged payloads. The id index
            # (startup snapshot + our own writes) answers this with no disk
            # probe; without an index, ask the backend. A concurrent *other*
            # process's write is invisible to the index, but writes upsert, so
            # the worst case is one redundant write.
            if self._persisted_ids is not None:
                fresh = [b for b in batch if b[0] not in self._persisted_ids]
            else:
                fresh = [b for b in batch if not self._backend.has(b[0])]
            if fresh:
                entries = [(nid, value, metadata, compute_ms)
                           for nid, value, metadata, _size, compute_ms in fresh]
                try:
                    if hasattr(self._backend, "put_success_batch"):
                        self._backend.put_success_batch(entries)
                    else:
                        raise NotImplementedError
                except Exception:  # noqa: BLE001 — one bad value must not sink the batch
                    for nid, value, metadata, compute_ms in entries:
                        try:
                            self._backend.put_success(nid, value, metadata=metadata, compute_ms=compute_ms)
                        except Exception:  # noqa: BLE001
                            logger.exception("async persistence failed for node %s", nid)
            if self._persisted_ids is not None:
                for nid, *_rest in batch:
                    self._persisted_ids.add(nid)
        except Exception:  # noqa: BLE001
            logger.exception("async persistence failed for batch of %d (first: %s)",
                             len(batch), batch[0][0])
        finally:
            written = sum(size for _nid, _value, _metadata, size, _cms in batch)
            batch.clear()  # drop the references: collectible once evicted
            with self._lock:
                self._pending_bytes -= written
                if self._pending_bytes <= 0:
                    self._pending_bytes = 0
                    self._drained.set()
