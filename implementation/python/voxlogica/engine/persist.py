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
import os
import queue
import threading
from typing import Any

from voxlogica.buffer_pool import buffer_states, release_states, retain_states
from voxlogica.lazy.ir import NodeId
from voxlogica.storage import StorageBackend

logger = logging.getLogger(__name__)

# Diagnostic only (off unless the path is set): names the value the writer is
# serializing right now, so a SIGSEGV inside gzip identifies its own payload.
_TRACE_PATH = os.environ.get("VOXLOGICA_TRACE_PERSIST", "")


_TRACE_SLOT = 256          # bytes per writer thread
_TRACE_SLOTS = 32
_TRACE_MMAP = None
_TRACE_IDS: dict[int, int] = {}
_TRACE_LOCK = threading.Lock()


def _trace_mmap():
    """Shared mmap holding one 'currently serializing' record per writer.

    A file-based trace CANNOT be used here: writing + fsync per batch perturbs
    the writer timing enough that the SIGSEGV under investigation stops
    reproducing entirely (6 clean runs against a ~60%-per-run base rate). An
    mmap write is a plain store to memory — no syscall, no lock, no I/O — so it
    cannot reorder the race it is meant to observe, and the kernel still
    flushes the dirty page after the process dies, so the record survives the
    crash that produced it.
    """
    global _TRACE_MMAP
    if _TRACE_MMAP is None:
        with _TRACE_LOCK:
            if _TRACE_MMAP is None:
                import mmap
                size = _TRACE_SLOT * _TRACE_SLOTS
                with open(_TRACE_PATH, "wb") as handle:
                    handle.write(b"\0" * size)
                handle = open(_TRACE_PATH, "r+b")
                _TRACE_MMAP = mmap.mmap(handle.fileno(), size)
    return _TRACE_MMAP


def _payload_snapshot(value):
    """Copy a volumetric payload's bytes, or None when there is nothing to copy.

    Only image-like values alias ITK memory; scalars, bytes and sequences are
    already owned by Python and are left alone (returning None makes the
    encoder use its normal path).
    """
    try:
        np_fn = getattr(value, "np", None)
        if not callable(np_fn):
            return None
        array = np_fn()
        view = memoryview(array)
        if not view.c_contiguous:
            return None
        return memoryview(bytes(view.cast("B")))
    except Exception:  # noqa: BLE001 - never break a write over a snapshot
        return None


def _trace_batch(entries) -> None:
    try:
        buf = _trace_mmap()
        ident = threading.get_ident()
        slot = _TRACE_IDS.get(ident)
        if slot is None:
            slot = len(_TRACE_IDS) % _TRACE_SLOTS
            _TRACE_IDS[ident] = slot
        nid, value, metadata, _cms = entries[0]
        inner = getattr(value, "_views", None)
        record = (f"slot{slot} n={len(entries)} {nid[:12]} op={metadata.get('operator')} "
                  f"src={metadata.get('source')} type={type(value).__name__} "
                  f"views={','.join(inner) if inner else '-'}").encode()[:_TRACE_SLOT - 1]
        base = slot * _TRACE_SLOT
        buf[base:base + _TRACE_SLOT] = record.ljust(_TRACE_SLOT, b"\0")
    except Exception:  # noqa: BLE001 — diagnostics must never break the run
        pass


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
        self._queue: "queue.SimpleQueue[tuple[NodeId, Any, dict, int, float, tuple[Any, ...]] | None]" = queue.SimpleQueue()
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

    _LINEAGE = "\x00lineage"   # queue sentinel: a DAG batch, not a value

    def submit_lineage(self, rows) -> None:
        """Queue DAG rows for the writer threads. Never blocks, never fails a run."""
        if rows:
            self._queue.put((self._LINEAGE, rows, {}, 0, 0.0, (), None))

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
        leases = retain_states(buffer_states(value))
        # Snapshot HERE, on the event loop. See pod_codec.encode_for_storage:
        # the payload aliases ITK-owned memory that ITK frees on its own
        # schedule, so a writer thread compressing the live alias races a
        # worker's SimpleITK call and reads unmapped pages.
        self._queue.put((node_id, value, metadata, size, compute_ms, leases,
                         _payload_snapshot(value)))

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

    @staticmethod
    def _deprioritize_self() -> None:
        """Run this writer at batch priority: background, never preempting a kernel.

        Persistence is latency-insensitive by construction — the engine never
        waits on it (submit is non-blocking, and a value the writer has not
        reached yet is simply recomputed). Compute is the opposite: a kernel
        that loses its core stalls the frontier. At the shipped defaults the
        writers are 4 threads competing on equal terms with 16 kernel workers
        for 24 cores, which is exactly backwards.

        SCHED_BATCH tells the scheduler this thread is throughput-oriented and
        not interactive: it keeps a full share when cores are idle, and yields
        first when they are contended. Best-effort — unavailable off Linux, and
        a restricted environment may refuse it; neither is worth failing over.
        """
        try:
            os.sched_setscheduler(0, os.SCHED_BATCH, os.sched_param(0))
        except (AttributeError, OSError, ValueError):
            pass

    def _run(self) -> None:
        self._deprioritize_self()
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

    def _write_batch(self, batch) -> None:
        lineage = [item for item in batch if item[0] is self._LINEAGE]
        if lineage:
            batch = [item for item in batch if item[0] is not self._LINEAGE]
            try:
                rows = [row for item in lineage for row in item[1]]
                put = getattr(self._backend, "put_lineage_batch", None)
                if put is not None:
                    put(rows)
            except Exception:  # noqa: BLE001 — lineage must never sink a run
                logger.exception("lineage write failed for %d rows", len(lineage))
            if not batch:
                return
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
                entries = [(nid, value, metadata, compute_ms, snap)
                           for nid, value, metadata, _size, compute_ms, _leases, snap in fresh]
                if _TRACE_PATH:
                    # Diagnostic for the SIGSEGV inside gzip (see the note on
                    # submit): record what is about to be serialized, flushed,
                    # so the file names the exact value the writer died on.
                    _trace_batch(entries)
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
            written = sum(item[3] for item in batch)
            for leases in (item[5] for item in batch):
                release_states(leases)
            batch.clear()  # drop the references: collectible once evicted
            with self._lock:
                self._pending_bytes -= written
                if self._pending_bytes <= 0:
                    self._pending_bytes = 0
                    self._drained.set()
