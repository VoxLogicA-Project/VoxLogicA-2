"""Bounded reuse pool for exclusive volumetric output buffers.

Buffers return here only after every engine live-tier owner and asynchronous
persistence owner releases its lease.  Pooling is representation-agnostic:
SimpleITK native outputs and NumPy/Numba outputs use the same lifetime protocol
with keys that include backend, shape, dtype/pixel type, and layout.
"""

from __future__ import annotations

from collections import defaultdict
import os
import threading
from typing import Any, Iterable
import weakref


_STATE_ATTR = "_voxlogica_buffer_pool_state"
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
}
_POOLED_NDARRAY_CLS: Any = None


def _limit_bytes() -> int:
    raw = os.environ.get("VOXLOGICA_BUFFER_POOL_MB", "512").strip()
    try:
        return max(0, int(float(raw) * 1024 * 1024))
    except ValueError:
        return 512 * 1024 * 1024


def _per_key_limit() -> int:
    raw = os.environ.get("VOXLOGICA_BUFFER_POOL_PER_KEY", "16").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 16


class BufferState:
    """One reusable allocation plus the number of outstanding engine leases."""

    __slots__ = ("kind", "key", "buffer_ref", "pooled_buffer", "nbytes", "leases", "pooled")

    def __init__(self, kind: str, key: tuple[Any, ...], buffer: Any, nbytes: int):
        self.kind = kind
        self.key = key
        self.buffer_ref = weakref.ref(buffer)
        self.pooled_buffer = None
        self.nbytes = int(nbytes)
        self.leases = 0
        self.pooled = False

    def current_buffer(self) -> Any | None:
        return self.pooled_buffer if self.pooled_buffer is not None else self.buffer_ref()

    def retain(self) -> None:
        with _LOCK:
            if self.pooled:
                raise RuntimeError("cannot retain a buffer while it is pooled")
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
    state.pooled = False
    state.pooled_buffer = None
    _POOLED_BYTES -= state.nbytes
    _STATS["reuses"] += 1
    _STATS[f"{state.kind}_reuses"] += 1
    return state, buffer


def _return_locked(state: BufferState) -> None:
    global _POOLED_BYTES, _PEAK_POOLED_BYTES
    limit = _limit_bytes()
    bucket = _POOLS[state.key]
    if limit <= 0 or state.nbytes > limit or len(bucket) >= _per_key_limit() \
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
            itemsize = _sitk_pixel_itemsize(pixel_id)
            state = BufferState("sitk", key, image, _product(size) * itemsize)
            setattr(image, _STATE_ATTR, state)
            _STATS["allocations"] += 1
            _STATS["sitk_allocations"] += 1
        else:
            _state, image = pooled
    image.CopyInformation(reference)
    return image


def _product(values: Iterable[int]) -> int:
    result = 1
    for value in values:
        result *= int(value)
    return result


def _sitk_pixel_itemsize(pixel_id: int) -> int:
    import SimpleITK as sitk

    sizes = {
        sitk.sitkInt8: 1, sitk.sitkUInt8: 1,
        sitk.sitkInt16: 2, sitk.sitkUInt16: 2,
        sitk.sitkInt32: 4, sitk.sitkUInt32: 4, sitk.sitkFloat32: 4,
        sitk.sitkInt64: 8, sitk.sitkUInt64: 8, sitk.sitkFloat64: 8,
    }
    return sizes[int(pixel_id)]


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


def recycle_unleased_states(states: Iterable[BufferState]) -> int:
    """Return scratch allocations that never entered the live tier."""
    recycled = 0
    with _LOCK:
        for state in states:
            if state.leases == 0 and not state.pooled and state.current_buffer() is not None:
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
