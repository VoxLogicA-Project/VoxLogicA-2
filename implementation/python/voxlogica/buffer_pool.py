"""Bounded reuse pool for exclusive volumetric output buffers.

Buffers return here only after every engine live-tier owner and asynchronous
persistence owner releases its lease.  Pooling is representation-agnostic:
SimpleITK native outputs and NumPy/Numba outputs use the same lifetime protocol
with keys that include backend, shape, dtype/pixel type, and layout.
"""

from __future__ import annotations

from collections import defaultdict
import os
import sys
import threading
from typing import Any, Iterable
import weakref


_STATE_ATTR = "_voxlogica_buffer_pool_state"
#: Where arrays.pinned_view caches the numpy view built over a sitk image.
#: Owned there, cleared HERE: a recycled image keeps its Python identity, so a
#: view built during its previous life would otherwise still be reachable from
#: it and would describe the wrong value.
VIEW_ATTR = "_voxlogica_pinned_view"
_LOCK = threading.RLock()
_POOLS: dict[tuple[Any, ...], list["BufferState"]] = defaultdict(list)
_POOLED_BYTES = 0
_PEAK_POOLED_BYTES = 0
_STATS = {
    "allocations": 0,
    "reuses": 0,
    "returns": 0,
    "drops": 0,
    "numpy_allocations": 0,
    "numpy_reuses": 0,
    "sitk_allocations": 0,
    "sitk_reuses": 0,
    # Invariant violations caught rather than left to corrupt memory.
    "leased_reuse_blocked": 0,
    "unsafe_recycle_blocked": 0,
    # Pooled buffers taken back by their own holder rather than by a new
    # allocation -- see BufferState.retain.
    "resurrections": 0,
}
_POOLED_NDARRAY_CLS: Any = None


_DEFAULT_LIMIT_BYTES = 512 * 1024 * 1024
_LIMIT_BYTES: int | None = None   # set by the engine from its memory budget


def set_limit_bytes(limit: int) -> None:
    """Size the pool from the engine's memory budget (see ComputationEngine).

    A fixed default cannot fit this workload: the pool's whole job is to keep
    freed volumes recyclable rather than handing them back to the allocator,
    and a BraTS volume is 9-35 MB, so 512 MB holds ~20 of them against a live
    tier of tens of GB. Pooled bytes are counted in `NodeTable.accounted_bytes`
    and trimmed by admission before its ceiling, so a larger pool is budgeted
    memory, not hidden memory.
    """
    global _LIMIT_BYTES
    _LIMIT_BYTES = max(0, limit)


def _limit_bytes() -> int:
    """The pool's byte limit: explicit configuration first, then the engine's.

    PRECEDENCE MATTERS AND WAS THE WRONG WAY ROUND. The engine calls
    ``set_limit_bytes`` unconditionally at startup (ComputationEngine.__init__),
    so a value set through the environment was read only when no engine had run
    -- which is never, on the engine path. VOXLOGICA_BUFFER_POOL_MB was
    therefore a documented knob that silently did nothing, and a run launched
    with VOXLOGICA_BUFFER_POOL_MB=0 to work around VoxLogicA-2#50 pooled buffers
    exactly as before and hit the same crash two and a half hours later.

    An explicit request from whoever launched the run outranks a value the engine
    derived for itself. The engine's figure remains the default, which is what it
    is for.
    """
    raw = os.environ.get("VOXLOGICA_BUFFER_POOL_MB", "").strip()
    if raw:
        try:
            return max(0, int(float(raw) * 1024 * 1024))
        except ValueError:
            pass
    if _LIMIT_BYTES is not None:
        return _LIMIT_BYTES
    return _DEFAULT_LIMIT_BYTES


def _per_key_limit(nbytes: int = 0) -> int:
    """How many buffers of ONE shape/dtype key the pool may hold.

    Derived from the byte limit rather than fixed, because this workload is
    nearly single-shaped: every volume in a BraTS sweep shares one key, so a
    flat count of 16 capped the pool at ~16 volumes however large its byte
    budget was — the count bound, not the byte bound, was what sent freed
    buffers back to the allocator (measured: 3,842 drops in a 4-minute run,
    against 5.5 BILLION minor page faults on a full one, i.e. the fresh pages
    those drops forced the kernel to fault back in).
    """
    raw = os.environ.get("VOXLOGICA_BUFFER_POOL_PER_KEY", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if nbytes <= 0:
        return 16
    return max(16, _limit_bytes() // nbytes)  # bytes are the real bound


class BufferState:
    """One reusable allocation plus the number of outstanding engine leases."""

    __slots__ = ("kind", "key", "buffer_ref", "pooled_buffer", "nbytes", "leases",
                 "pooled", "recycled")

    def __init__(self, kind: str, key: tuple[Any, ...], buffer: Any, nbytes: int):
        self.kind = kind
        self.key = key
        self.buffer_ref = weakref.ref(buffer)
        self.pooled_buffer = None
        self.nbytes = int(nbytes)
        self.leases = 0
        self.pooled = False
        #: True once this state's buffer has been handed to a DIFFERENT value.
        #: The state is then a tombstone: whoever still reaches it is holding a
        #: reference to memory that now belongs to someone else, and must be
        #: told so rather than allowed to lease it. See _take_locked.
        self.recycled = False

    def current_buffer(self) -> Any | None:
        return self.pooled_buffer if self.pooled_buffer is not None else self.buffer_ref()

    def retain(self) -> None:
        """Take a lease, resurrecting the buffer from the pool if it is there.

        WHY RESURRECTING IS THE FIX AND RAISING WAS NOT. `buffer_states` walks
        into containers, so one buffer is reachable from several values -- a
        loop body's element and the sequence assembled from it, for instance --
        while leases are taken per node. The element can therefore be evicted,
        drop the last lease and be pooled while a container that still
        references it is alive. Retaining through that container then found a
        pooled state and raised, killing runs hours in (VoxLogicA-2#50).

        Raising was diagnosing the wrong half of the problem. A state that is
        pooled and unleased is held by nobody: under _LOCK it can simply be
        taken back, and doing so is not merely safe but *protective*, because it
        removes the buffer from the pool before some later allocation can be
        handed it while the original holder still points at it. That silent
        aliasing is the real hazard, and it is what the old guard let through:
        by the time the buffer has been recycled the state reads pooled=False
        again, so retain succeeded and two values shared one allocation.

        The dangerous case now raises instead, with an accurate message.
        """
        with _LOCK:
            if self.recycled:
                raise RuntimeError(
                    f"buffer-pool: this allocation was recycled for another value "
                    f"and no longer holds what the caller expects "
                    f"(kind={self.kind} key={self.key}); the reference is stale")
            if self.pooled:
                _unpool_locked(self)
            self.leases += 1

    def release(self) -> None:
        with _LOCK:
            if self.leases <= 0:
                raise RuntimeError("buffer-pool lease released more than once")
            self.leases -= 1
            if self.leases == 0:
                _return_locked(self)


def _pooled_ndarray_cls():
    global _POOLED_NDARRAY_CLS
    if _POOLED_NDARRAY_CLS is None:
        import numpy as np

        class PooledNDArray(np.ndarray):
            def __array_finalize__(self, source):
                if source is not None:
                    setattr(self, _STATE_ATTR, getattr(source, _STATE_ATTR, None))

        _POOLED_NDARRAY_CLS = PooledNDArray
    return _POOLED_NDARRAY_CLS


def _unpool_locked(state: "BufferState") -> None:
    """Take a pooled, unleased buffer back out of the pool for its own holder."""
    global _POOLED_BYTES
    bucket = _POOLS.get(state.key)
    if bucket:
        try:
            bucket.remove(state)
        except ValueError:
            pass
        if not bucket:
            _POOLS.pop(state.key, None)
    state.pooled = False
    state.pooled_buffer = None
    _POOLED_BYTES -= state.nbytes
    _STATS["resurrections"] += 1


def _take_locked(key: tuple[Any, ...]) -> tuple[BufferState, Any] | None:
    global _POOLED_BYTES
    bucket = _POOLS.get(key)
    if not bucket:
        return None
    state = bucket.pop()
    if not bucket:
        _POOLS.pop(key, None)
    buffer = state.current_buffer()
    if buffer is None:
        raise RuntimeError("pooled buffer disappeared")
    # INVARIANT: a pooled buffer has no outstanding owners. Handing out one
    # that is still leased is the use-after-free that segfaults the persister
    # thread inside gzip (see engine/persist.py). Fail loudly and locally
    # instead of corrupting memory and dying somewhere unrelated.
    if state.leases != 0:
        _STATS["leased_reuse_blocked"] += 1
        raise RuntimeError(
            f"buffer-pool invariant: took a pooled buffer with {state.leases} "
            f"outstanding lease(s), kind={state.kind} key={state.key}")
    state.pooled = False
    state.pooled_buffer = None
    _POOLED_BYTES -= state.nbytes
    _STATS["reuses"] += 1
    _STATS[f"{state.kind}_reuses"] += 1
    # The buffer object is reused, so anything still pointing at it from its
    # PREVIOUS value now points at someone else's data. Give the new owner a
    # fresh state and leave the old one as a tombstone, so a stale holder that
    # tries to lease it is told the truth instead of silently sharing the
    # allocation. Reusing one state object for successive values is what made
    # that sharing invisible.
    state.recycled = True
    fresh = BufferState(state.kind, state.key, buffer, state.nbytes)
    try:
        setattr(buffer, _STATE_ATTR, fresh)
    except AttributeError:
        # Not all buffer kinds accept attributes; such a buffer keeps the old
        # state, which is exactly the pre-existing behaviour.
        state.recycled = False
        return state, buffer
    return fresh, buffer


def _return_locked(state: BufferState) -> None:
    global _POOLED_BYTES, _PEAK_POOLED_BYTES
    if state.recycled:
        # A tombstone: its buffer belongs to another value now, so returning it
        # would offer the same allocation to the pool twice.
        state.pooled_buffer = None
        return
    limit = _limit_bytes()
    bucket = _POOLS[state.key]
    if limit <= 0 or state.nbytes > limit or len(bucket) >= _per_key_limit(state.nbytes) \
            or _POOLED_BYTES + state.nbytes > limit:
        _STATS["drops"] += 1
        state.pooled_buffer = None
        if not bucket:
            _POOLS.pop(state.key, None)
        return
    state.pooled = True
    state.pooled_buffer = state.buffer_ref()
    if state.pooled_buffer is None:
        state.pooled = False
        _STATS["drops"] += 1
        if not bucket:
            _POOLS.pop(state.key, None)
        return
    bucket.append(state)
    _POOLED_BYTES += state.nbytes
    _PEAK_POOLED_BYTES = max(_PEAK_POOLED_BYTES, _POOLED_BYTES)
    _STATS["returns"] += 1


def acquire_numpy(shape: tuple[int, ...], dtype: Any) -> Any:
    """Exclusive C-contiguous NumPy output; contents are unspecified."""
    import numpy as np

    normalized_shape = tuple(int(v) for v in shape)
    normalized_dtype = np.dtype(dtype)
    if _limit_bytes() <= 0:
        with _LOCK:
            _STATS["allocations"] += 1
            _STATS["numpy_allocations"] += 1
        return np.empty(normalized_shape, dtype=normalized_dtype)
    key = ("numpy", normalized_shape, normalized_dtype.str, "C")
    with _LOCK:
        pooled = _take_locked(key)
        if pooled is None:
            array = np.empty(normalized_shape, dtype=normalized_dtype).view(_pooled_ndarray_cls())
            state = BufferState("numpy", key, array, array.nbytes)
            setattr(array, _STATE_ATTR, state)
            _STATS["allocations"] += 1
            _STATS["numpy_allocations"] += 1
        else:
            _state, array = pooled
    return array


def acquire_sitk(reference: Any, pixel_id: int) -> Any:
    """Exclusive scalar SimpleITK output; contents are unspecified."""
    size = tuple(int(v) for v in reference.GetSize())
    if _limit_bytes() <= 0:
        import SimpleITK as sitk

        with _LOCK:
            _STATS["allocations"] += 1
            _STATS["sitk_allocations"] += 1
        image = sitk.Image(size, pixel_id)
        image.CopyInformation(reference)
        return image
    key = ("sitk", size, int(pixel_id), 1)
    with _LOCK:
        pooled = _take_locked(key)
        if pooled is None:
            import SimpleITK as sitk

            image = sitk.Image(size, pixel_id)
            itemsize = sitk_itemsize(pixel_id)
            state = BufferState("sitk", key, image, _product(size) * itemsize)
            setattr(image, _STATE_ATTR, state)
            _STATS["allocations"] += 1
            _STATS["sitk_allocations"] += 1
        else:
            _state, image = pooled
            # The image object is REUSED, not reallocated, so anything keyed on
            # its identity survives into the next value and must go.
            try:
                delattr(image, VIEW_ATTR)
            except AttributeError:
                pass
        # Inside the lock: this mutates an ITK object that was, an instant ago,
        # reachable from the shared pool.
        image.CopyInformation(reference)
    return image


def _product(values: Iterable[int]) -> int:
    result = 1
    for value in values:
        result *= int(value)
    return result


_PIXEL_ITEMSIZE: dict[int, int] | None = None


def sitk_itemsize(pixel_id: int, default: int | None = None) -> int:
    """Bytes per scalar component of a SimpleITK pixel type.

    The one table. It lived here for the pool's own sizing and again in
    arrays.py for the engine's byte accounting, which is two chances for a
    pixel type to be added to one and not the other and for resident bytes to
    be misreported by a factor of two.

    ``default`` distinguishes the two callers: accounting must never crash on
    an unknown type (it passes a fallback), while allocating a buffer of an
    unknown size must (it does not).
    """
    global _PIXEL_ITEMSIZE
    if _PIXEL_ITEMSIZE is None:
        import SimpleITK as sitk

        _PIXEL_ITEMSIZE = {
            sitk.sitkInt8: 1, sitk.sitkUInt8: 1, sitk.sitkLabelUInt8: 1,
            sitk.sitkVectorInt8: 1, sitk.sitkVectorUInt8: 1,
            sitk.sitkInt16: 2, sitk.sitkUInt16: 2, sitk.sitkLabelUInt16: 2,
            sitk.sitkVectorInt16: 2, sitk.sitkVectorUInt16: 2,
            sitk.sitkInt32: 4, sitk.sitkUInt32: 4, sitk.sitkLabelUInt32: 4,
            sitk.sitkVectorInt32: 4, sitk.sitkVectorUInt32: 4,
            sitk.sitkFloat32: 4, sitk.sitkVectorFloat32: 4, sitk.sitkComplexFloat32: 8,
            sitk.sitkInt64: 8, sitk.sitkUInt64: 8, sitk.sitkLabelUInt64: 8,
            sitk.sitkVectorInt64: 8, sitk.sitkVectorUInt64: 8,
            sitk.sitkFloat64: 8, sitk.sitkVectorFloat64: 8, sitk.sitkComplexFloat64: 16,
        }
    size = _PIXEL_ITEMSIZE.get(int(pixel_id), default)
    if size is None:
        raise KeyError(f"unsupported SimpleITK pixel id for allocation: {pixel_id}")
    return size


def state_of(value: Any) -> BufferState | None:
    state = getattr(value, _STATE_ATTR, None)
    return state if isinstance(state, BufferState) else None


def buffer_states(value: Any) -> tuple[BufferState, ...]:
    """Unique pooled allocations reachable from one runtime value."""
    found: dict[int, BufferState] = {}
    seen: set[int] = set()

    def visit(item: Any) -> None:
        item_id = id(item)
        if item_id in seen:
            return
        seen.add(item_id)
        state = state_of(item)
        if state is not None:
            found[id(state)] = state
        provider = getattr(item, "_buffer_pool_states", None)
        if callable(provider):
            for provided in provider():
                if isinstance(provided, BufferState):
                    found[id(provided)] = provided
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(found.values())


def retain_states(states: Iterable[BufferState]) -> tuple[BufferState, ...]:
    retained = tuple(states)
    for state in retained:
        state.retain()
    return retained


def release_states(states: Iterable[BufferState]) -> None:
    for state in states:
        state.release()


def trim_pool(target_bytes: int = 0) -> int:
    """Drop cached allocations until pooled bytes are at most ``target_bytes``."""
    global _POOLED_BYTES
    target = max(0, int(target_bytes))
    dropped = 0
    with _LOCK:
        for key in list(_POOLS):
            bucket = _POOLS[key]
            while bucket and _POOLED_BYTES > target:
                state = bucket.pop()
                state.pooled = False
                state.pooled_buffer = None
                _POOLED_BYTES -= state.nbytes
                dropped += 1
            if not bucket:
                _POOLS.pop(key, None)
            if _POOLED_BYTES <= target:
                break
    return dropped


def pool_stats() -> dict[str, int]:
    with _LOCK:
        return {
            **_STATS,
            "pooled_buffers": sum(len(bucket) for bucket in _POOLS.values()),
            "pooled_bytes": _POOLED_BYTES,
            "peak_pooled_bytes": _PEAK_POOLED_BYTES,
        }


def pooled_bytes() -> int:
    with _LOCK:
        return _POOLED_BYTES


def pooled_bytes_approx() -> int:
    """Pooled bytes WITHOUT taking the pool lock — for hot-path budget checks.

    `_POOLED_BYTES` is a plain int, so this read is atomic; it may be a few
    allocations stale, which is irrelevant to a budget comparison that is a
    heuristic anyway. Taking the lock here is not: this is read from the
    scheduler's admission and reclaim paths, which run on every worker turn,
    against the same lock every worker needs to allocate or return a buffer.
    Contending the pool lock to read an advisory number serialises the workers
    against the event loop for no benefit.
    """
    return _POOLED_BYTES


def recycle_unleased_states(states: Iterable[BufferState], *,
                            extra_refs: int = 0) -> int:
    """Return scratch allocations that never entered the live tier.

    ``leases == 0`` alone is NOT a safe test for "nobody is using this".  A
    buffer has zero leases for the whole window between ``acquire_*`` and the
    event loop's ``NodeTable.set_value``, so anything still holding the Python
    object — a fusion cone's exit value on its way back to the scheduler, a
    view handed to another kernel — is invisible to a lease check. Recycling
    there hands a live buffer to the next allocation, which is the
    use-after-free that segfaults the persister inside gzip.

    The refcount is the direct test the lease count cannot give: if anything
    beyond this function's own temporaries still references the object, leave
    it alone. ``extra_refs`` lets the caller declare references it is still
    holding deliberately (its own scratch dict, for instance).
    """
    recycled = 0
    with _LOCK:
        for state in states:
            if state.leases != 0 or state.pooled:
                continue
            buffer = state.current_buffer()
            if buffer is None:
                continue
            # `buffer` local + getrefcount's own argument = 2 baseline.
            if sys.getrefcount(buffer) > 2 + extra_refs:
                _STATS["unsafe_recycle_blocked"] += 1
                continue
            _return_locked(state)
            recycled += int(state.pooled)
    return recycled


def reset_pool_for_tests() -> None:
    global _POOLED_BYTES, _PEAK_POOLED_BYTES
    trim_pool(0)
    with _LOCK:
        _POOLED_BYTES = 0
        _PEAK_POOLED_BYTES = 0
        for key in _STATS:
            _STATS[key] = 0
