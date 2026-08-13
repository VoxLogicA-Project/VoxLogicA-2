"""PolyArray: one volumetric value, many zero-copy views.

Kernels today speak SimpleITK exclusively; fused kernels (``engine/fusion.py``)
speak numpy/numba, and a future GPU site speaks device arrays. Without a
shared value type, every kernel boundary would force a conversion and every
consumer would need to know which library produced its input. ``PolyArray``
is that shared type: it holds a canonical buffer plus geometry and builds
other views lazily. Views remain cached unless a representation transition
explicitly releases the old independent buffer.

HONEST CONSTRAINTS (do not paper over these):
- sitk -> numpy is zero-copy but read-only (``GetArrayViewFromImage``): a
  kernel that mutates a numpy view obtained this way corrupts the sitk image
  in place (see ``is_readonly_np``). Fused/numba code must write to a fresh
  or pooled buffer, never through ``.np()`` when that flag is set.
- numpy -> sitk always copies (SimpleITK owns its buffers; it cannot wrap a
  foreign one). This is unavoidable, only avoidable to *cross less often* —
  a chain of fused kernels should stay in numpy end-to-end and only pay this
  once, when a legacy sitk kernel finally consumes the result. At that real
  boundary the engine drops the old independent numpy cache; a later numpy
  request becomes a zero-copy alias of the new SimpleITK canonical buffer.
- ``nbytes`` sums every resident view's footprint (host + device), because
  the engine's memory accounting (``NodeTable``/admission) must see the true
  resident cost, not just one view of it. A numpy view and the sitk view
  built from it are two independent buffers (the copy above) and both count.
"""

from __future__ import annotations

import os
import threading
import weakref
from dataclasses import dataclass
from typing import Any

from voxlogica.buffer_pool import (VIEW_ATTR as _VIEW_ATTR, acquire_sitk,
                                   sitk_itemsize, state_of)

_sitk = None


def _simpleitk():
    global _sitk
    if _sitk is None:
        import SimpleITK
        _sitk = SimpleITK
    return _sitk


def _sitk_itemsize(image: Any) -> int:
    """Bytes per scalar component; 4 on an unknown type, never crash accounting."""
    return sitk_itemsize(image.GetPixelID(), default=4)


def _sitk_nbytes(image: Any) -> int:
    """Byte footprint of a sitk image from pure metadata — no array view built."""
    return image.GetNumberOfPixels() * image.GetNumberOfComponentsPerPixel() * _sitk_itemsize(image)


# ── Zero-copy sitk -> numpy, without the use-after-free ──────────────────────
#
# ``GetArrayViewFromImage`` aliases the image's buffer but does NOT hold a
# reference to the image: the returned array's ``.base`` is an intermediate
# ndarray, so when the last Python reference to the image drops — typically a
# temporary produced by a cast or a filter — the buffer is freed and the view
# dangles. Verified on SimpleITK 2.5.5: reading such a view after the source
# is collected yields garbage (NaN) rather than raising. Pinning the image
# onto the view closes that hole and makes a returned view safe.

_PINNED_CLS: Any = None


def _pinned_cls() -> Any:
    """The ndarray subclass used for pinned views (built lazily: defining it
    requires numpy, and this module keeps its heavy imports lazy)."""
    global _PINNED_CLS
    if _PINNED_CLS is None:
        import numpy as np

        class PinnedArray(np.ndarray):
            """A view that keeps the object owning its buffer alive."""

            def __array_finalize__(self, obj):
                if obj is not None:
                    self._src = getattr(obj, "_src", None)

        _PINNED_CLS = PinnedArray
    return _PINNED_CLS


#: The cache is a WEAK reference: strong would close a cycle (image -> view ->
#: _src -> image) and hand every volume to the cycle collector instead of
#: letting it die on its last release. The attribute name lives in buffer_pool
#: because that is where a recycled image has to forget it (see acquire_sitk).
#: Serialises view CONSTRUCTION only; cache hits never take it.
_VIEW_LOCK = threading.Lock()


def pinned_view(image: Any) -> Any:
    """Zero-copy, read-only numpy view of ``image`` that pins it alive.

    Prefer this over a bare ``GetArrayViewFromImage`` whenever the view may
    outlive the expression that produced the image. Read-only: writing through
    it would corrupt the sitk-owned buffer, so a writer must take a copy.

    BUILT AT MOST ONCE PER IMAGE, AND UNDER A LOCK, BECAUSE THIS IS A WRITE.
    ``GetArrayViewFromImage`` calls ``image.MakeUnique()``, which detaches
    SimpleITK's copy-on-write buffer and reassigns the internal smart pointer —
    a mutation, however much the call reads like a read. Kernels call this
    straight on their INPUTS (``pinned_view(left)``, 22 sites), and an input is
    a shared, table-resident value that every worker needing it reads at the
    same moment. With the GIL gone nothing serialised those calls, so N threads
    ran MakeUnique on one image concurrently. Measured twice in one day, both
    with the same stack: SIGSEGV inside MakeUnique 69 minutes into a sweep, and
    `corrupted size vs prev_size` — glibc's double-free signature — 2 minutes
    into a program whose only difference was reading each cached input from ~28
    places at once instead of one. The time-to-crash fell 30x as the number of
    concurrent readers per value rose, which is what a race proportional to
    sharing looks like.

    Caching also makes the common path cheaper than it was: a repeat reader now
    pays one attribute lookup instead of a fresh view construction.
    """
    cached_ref = getattr(image, _VIEW_ATTR, None)
    if cached_ref is not None:
        cached = cached_ref()
        if cached is not None:
            return cached
    with _VIEW_LOCK:
        # Re-check: another thread may have built it while we waited.
        cached_ref = getattr(image, _VIEW_ATTR, None)
        if cached_ref is not None:
            cached = cached_ref()
            if cached is not None:
                return cached
        sitk = _simpleitk()
        view = sitk.GetArrayViewFromImage(image).view(_pinned_cls())
        view._src = image
        try:
            setattr(image, _VIEW_ATTR, weakref.ref(view))
        except (AttributeError, TypeError):
            pass  # an image that refuses attributes still gets a correct view
        return view


# ── Fresh SimpleITK output -> writable NumPy alias ──────────────────────────
#
# SimpleITK deliberately exposes Python array views as read-only.  Its C++
# API has mutable buffer accessors, but they are not wrapped for Python.  The
# small, tightly contained escape hatch below is used only for a *newly
# allocated, exclusively-owned* output image: NumPy writes the result directly
# into the output image's storage, so the next ITK primitive receives a native
# sitk.Image without a numpy -> sitk copy.
#
# This is runtime-verified rather than assumed.  A future SimpleITK release
# which changes its buffer protocol disables this fast path at the caller;
# kernels then use their established SimpleITK implementation.  Do not call
# writable_view on an input or otherwise shared image: external writes bypass
# SimpleITK's copy-on-write bookkeeping.


class WritableViewUnavailable(RuntimeError):
    """The installed SimpleITK cannot safely support the native-output path."""


_WRITABLE_PIXEL_DTYPES: dict[int, Any] | None = None
_WRITABLE_PIXEL_VALIDATION: dict[int, bool | str] = {}
_WRITABLE_PIXEL_VALIDATION_LOCK = threading.Lock()


def writable_sitk_output_mode() -> str:
    """Return ``auto``, ``off``, or ``required`` for the native-output path."""
    raw = os.environ.get("VOXLOGICA_WRITABLE_SITK_OUTPUT", "auto").strip().lower()
    aliases = {"0": "off", "false": "off", "1": "auto", "true": "auto", "on": "auto"}
    mode = aliases.get(raw, raw)
    if mode not in {"auto", "off", "required"}:
        raise WritableViewUnavailable(
            "VOXLOGICA_WRITABLE_SITK_OUTPUT must be auto, off, or required"
        )
    return mode


def _writable_pixel_dtypes() -> dict[int, Any]:
    """Explicit scalar pixel-id whitelist for writable output aliases."""
    global _WRITABLE_PIXEL_DTYPES
    if _WRITABLE_PIXEL_DTYPES is None:
        import numpy as np

        sitk = _simpleitk()
        _WRITABLE_PIXEL_DTYPES = {
            sitk.sitkInt8: np.dtype(np.int8),
            sitk.sitkUInt8: np.dtype(np.uint8),
            sitk.sitkInt16: np.dtype(np.int16),
            sitk.sitkUInt16: np.dtype(np.uint16),
            sitk.sitkInt32: np.dtype(np.int32),
            sitk.sitkUInt32: np.dtype(np.uint32),
            sitk.sitkInt64: np.dtype(np.int64),
            sitk.sitkUInt64: np.dtype(np.uint64),
            sitk.sitkFloat32: np.dtype(np.float32),
            sitk.sitkFloat64: np.dtype(np.float64),
        }
    return _WRITABLE_PIXEL_DTYPES


def _raw_writable_view(image: Any) -> Any:
    """Build a pinned writable alias after all safety checks have passed."""
    import numpy as np

    ro = _simpleitk().GetArrayViewFromImage(image)
    expected_dtype = _writable_pixel_dtypes().get(image.GetPixelID())
    if expected_dtype is None or ro.dtype != expected_dtype:
        raise WritableViewUnavailable(f"Unsupported SimpleITK pixel type: {image.GetPixelIDTypeAsString()}")
    if not ro.flags.c_contiguous:
        raise WritableViewUnavailable("SimpleITK array view is not C-contiguous")
    if ro.nbytes != image.GetNumberOfPixels() * expected_dtype.itemsize:
        raise WritableViewUnavailable("SimpleITK array view has an unexpected byte size")
    try:
        address, _readonly = ro.__array_interface__["data"]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise WritableViewUnavailable("SimpleITK array view has no usable data pointer") from exc
    if not address:
        raise WritableViewUnavailable("SimpleITK array view has a null data pointer")
    try:
        ctype = np.ctypeslib.as_ctypes_type(expected_dtype)
        raw = np.ctypeslib.as_array((ctype * ro.size).from_address(address)).reshape(ro.shape)
    except (NotImplementedError, TypeError, ValueError) as exc:
        raise WritableViewUnavailable("Cannot construct writable NumPy alias") from exc
    view = raw.view(_pinned_cls())
    view._src = image
    return view


def _validate_writable_pixel_type(pixel_id: int) -> None:
    """Prove write-through for one fresh image of ``pixel_id`` exactly once."""
    with _WRITABLE_PIXEL_VALIDATION_LOCK:
        prior = _WRITABLE_PIXEL_VALIDATION.get(pixel_id)
        if prior is True:
            return
        if isinstance(prior, str):
            raise WritableViewUnavailable(prior)
        sitk = _simpleitk()
        dtype = _writable_pixel_dtypes().get(pixel_id)
        if dtype is None:
            raise WritableViewUnavailable(f"Unsupported SimpleITK pixel type: {pixel_id}")
        try:
            probe = sitk.Image([2, 2], pixel_id)
            view = _raw_writable_view(probe)
            sentinel = dtype.type(7.5 if dtype.kind == "f" else 7)
            view.fill(sentinel)
            observed = sitk.GetArrayViewFromImage(probe)
            if not (observed == sentinel).all():
                raise WritableViewUnavailable("SimpleITK did not observe a writable alias write")
        except Exception as exc:
            message = f"SimpleITK writable-buffer validation failed for pixel type {pixel_id}: {exc}"
            _WRITABLE_PIXEL_VALIDATION[pixel_id] = message
            raise WritableViewUnavailable(message) from exc
        _WRITABLE_PIXEL_VALIDATION[pixel_id] = True


def writable_view(image: Any) -> Any:
    """Pinned writable alias of a fresh, exclusive scalar ``sitk.Image``.

    This is intentionally not a general mutation API.  Callers should use
    :func:`allocate_writable_like`, which creates the required fresh image.
    """
    if image.GetNumberOfComponentsPerPixel() != 1:
        raise WritableViewUnavailable("Writable aliases do not support vector images")
    _validate_writable_pixel_type(image.GetPixelID())
    return _raw_writable_view(image)


def allocate_writable_like(reference: Any, pixel_id: int) -> tuple[Any, Any]:
    """Acquire a native SimpleITK output and its private writable alias.

    The image is exclusively owned but may come from the bounded buffer pool;
    its pixels are unspecified and the caller must overwrite every element.
    It has no current consumers; retaining or mutating the alias after the
    kernel returns is forbidden.
    """
    if writable_sitk_output_mode() == "off":
        raise WritableViewUnavailable("Writable SimpleITK outputs are disabled")
    if reference.GetNumberOfComponentsPerPixel() != 1:
        raise WritableViewUnavailable("Writable aliases do not support vector images")
    if pixel_id not in _writable_pixel_dtypes():
        raise WritableViewUnavailable(f"Unsupported SimpleITK output pixel type: {pixel_id}")
    image = acquire_sitk(reference, pixel_id)
    return image, writable_view(image)


@dataclass(frozen=True)
class Geometry:
    """Spatial metadata carried alongside pixel data; hashable, sitk-shaped."""

    spacing: tuple[float, ...]
    origin: tuple[float, ...]
    direction: tuple[float, ...]
    components: int = 1

    @classmethod
    def from_sitk(cls, image: Any) -> "Geometry":
        return cls(
            spacing=tuple(float(v) for v in image.GetSpacing()),
            origin=tuple(float(v) for v in image.GetOrigin()),
            direction=tuple(float(v) for v in image.GetDirection()),
            components=int(image.GetNumberOfComponentsPerPixel()),
        )

    @classmethod
    def identity(cls, ndim: int) -> "Geometry":
        flat = [0.0] * (ndim * ndim)
        for i in range(ndim):
            flat[i * ndim + i] = 1.0
        return cls(
            spacing=tuple(1.0 for _ in range(ndim)),
            origin=tuple(0.0 for _ in range(ndim)),
            direction=tuple(flat),
        )


class PolyArray:
    """A volumetric value with lazily-built, cached views onto its data.

    Exactly one view is canonical (whichever the value was constructed
    from); every other view is built on first request and cached in
    ``_views``.
    """

    __slots__ = ("geometry", "dtype", "shape", "_views", "_readonly_np", "_view_lock")

    def __init__(self, geometry: Geometry, dtype: Any, shape: tuple[int, ...]):
        self.geometry = geometry
        self.dtype = dtype
        self.shape = shape
        self._views: dict[str, Any] = {}
        # True iff the cached "np" view is a read-only zero-copy alias of a
        # sitk-owned buffer — writing through it would corrupt the source image.
        self._readonly_np = False
        # A table-resident value is read by many concurrent consumers (every
        # node that depends on it, each on its own pool thread — or the event
        # loop thread, e.g. Stage B's shape_of). Building the FIRST view of a
        # kind is not just a Python dict write: it can call into sitk's own
        # C++ reference-counted image machinery (GetArrayViewFromImage), which
        # is not safe to enter concurrently for the same image from two
        # threads. Reentrant because .sitk() calls .np() on the same object.
        self._view_lock = threading.RLock()

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_sitk(cls, image: Any) -> "PolyArray":
        """Wrap a SimpleITK image. The numpy view is built lazily, on first
        ``.np()``, as a zero-copy read-only alias.

        ``shape`` is a best-effort spatial hint (sitk's size, axis-reversed
        to numpy order) until ``.np()`` actually builds the array view and
        corrects it — vector images gain a trailing components axis that
        sitk's ``GetSize()`` does not report.
        """
        shape = tuple(reversed(image.GetSize()))
        poly = cls(Geometry.from_sitk(image), None, shape)
        poly._views["sitk"] = image
        return poly

    @classmethod
    def from_numpy(cls, array: Any, geometry: Geometry | None = None) -> "PolyArray":
        """Wrap a numpy array the caller owns; writable, no sitk view yet."""
        if geometry is None:
            geometry = Geometry.identity(array.ndim)
        poly = cls(geometry, array.dtype, tuple(array.shape))
        poly._views["np"] = array
        poly._readonly_np = False
        return poly

    # ── Views ─────────────────────────────────────────────────────────────────

    def np(self):
        """Numpy view. Read-only iff it aliases a sitk-owned buffer — check
        ``is_readonly_np`` before writing through it."""
        cached = self._views.get("np")
        if cached is not None:
            return cached
        with self._view_lock:
            cached = self._views.get("np")
            if cached is not None:
                return cached
            sitk_image = self._views.get("sitk")
            if sitk_image is None:
                raise RuntimeError("PolyArray has no cached view to build numpy from")
            # PINNED, not a bare GetArrayViewFromImage: this alias owns no
            # memory, it points into the SimpleITK image's buffer, and it
            # routinely outlives the caller that asked for it — the persister
            # thread takes it via VoxImageValue.as_array() and then spends
            # milliseconds inside gzip reading those bytes, on a different
            # thread from every other owner. If the image it aliases is freed
            # in that window (the view cache dropped, the value evicted, the
            # last reference released), the read is into freed memory and the
            # process dies with a SIGSEGV inside gzip.compress with no Python
            # traceback to explain it. Pinning makes the view keep its source
            # image alive for exactly as long as the view exists, which is the
            # rule pinned_view() documents and the sibling branch of
            # as_array() already follows for raw sitk images.
            arr = pinned_view(sitk_image)
            self._views["np"] = arr
            self._readonly_np = True
            self.dtype = arr.dtype
            self.shape = tuple(arr.shape)
            return arr

    def sitk(self, *, retain_numpy: bool = True):
        """SimpleITK image view, copied from numpy on first request.

        ``retain_numpy=False`` makes the new SimpleITK buffer canonical by
        dropping an independently-owned numpy cache after the copy.  Future
        ``np()`` calls remain valid: they rebuild a read-only zero-copy alias
        of the SimpleITK buffer.  Engine adapters use this mode at a real
        numpy->SimpleITK boundary so a value does not remain double-resident
        merely in case a later consumer asks for numpy again.
        """
        cached = self._views.get("sitk")
        if cached is not None:
            if not retain_numpy:
                with self._view_lock:
                    if "np" in self._views and not self._readonly_np:
                        self._views.pop("np", None)
            return cached
        with self._view_lock:
            cached = self._views.get("sitk")
            if cached is not None:
                if not retain_numpy and "np" in self._views and not self._readonly_np:
                    self._views.pop("np", None)
                return cached
            sitk = _simpleitk()
            arr = self.np()
            image = sitk.GetImageFromArray(arr, isVector=self.geometry.components > 1)
            image.SetSpacing(self.geometry.spacing)
            image.SetOrigin(self.geometry.origin)
            image.SetDirection(self.geometry.direction)
            self._views["sitk"] = image
            if not retain_numpy and not self._readonly_np:
                self._views.pop("np", None)
            return image

    def __dlpack__(self, stream=None):
        """DLPack export via the numpy view — free interop with torch/tf/jax."""
        arr = self.np()
        return arr.__dlpack__(stream) if stream is not None else arr.__dlpack__()

    def __dlpack_device__(self):
        return self.np().__dlpack_device__()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_readonly_np(self) -> bool:
        """True iff writing through ``.np()`` would corrupt a sitk-owned buffer."""
        return self._readonly_np and "np" in self._views

    @property
    def nbytes(self) -> int:
        """Resident footprint across every cached view (host + device).

        Sized from whichever view is ALREADY cached — never by building a new
        one. This runs on every node completion (``NodeTable.complete`` ->
        ``approx_bytes`` -> here, for admission/eviction accounting), so
        forcing ``.np()`` just to answer a byte count would replace a cheap
        sitk metadata read with a real ``GetArrayViewFromImage`` call on every
        single node in a run — measured ~9x more expensive, and it dominated
        wall time in a fusion throughput benchmark before this was fixed
        (see doc/dev/dynamic-scheduler/frontier-scheduler.md).

        When only "sitk" is cached: pixels x components x itemsize from pure
        metadata, no array view built. When "np" is cached (whether as the
        sitk alias or a genuinely separate buffer), size from it directly —
        it's already resident, nothing new to build. If both are cached and
        NOT aliased (``.sitk()`` was built from an independently-owned numpy
        array), they are two independent same-sized buffers and both count.
        """
        if "np" in self._views:
            one_buffer = self._views["np"].nbytes
        else:
            one_buffer = _sitk_nbytes(self._views["sitk"])
        if "sitk" in self._views and "np" in self._views and not self._readonly_np:
            return one_buffer * 2
        return one_buffer

    def release_view(self, name: str) -> None:
        """Drop a cached view (e.g. after transferring off a device)."""
        if name == "np" and self._readonly_np:
            self._readonly_np = False
        self._views.pop(name, None)

    def resident_views(self) -> tuple[str, ...]:
        return tuple(self._views.keys())

    def _buffer_pool_states(self) -> tuple[Any, ...]:
        """Reusable allocations currently reachable through cached views."""
        states: dict[int, Any] = {}
        for view in self._views.values():
            state = state_of(view)
            if state is not None:
                states[id(state)] = state
        return tuple(states.values())
