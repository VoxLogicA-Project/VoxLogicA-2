"""PolyArray: one volumetric value, many zero-copy views.

Kernels today speak SimpleITK exclusively; fused kernels (``engine/fusion.py``)
speak numpy/numba, and a future GPU site speaks device arrays. Without a
shared value type, every kernel boundary would force a conversion and every
consumer would need to know which library produced its input. ``PolyArray``
is that shared type: it holds exactly one canonical buffer plus geometry, and
builds every other view lazily, on first request, cached thereafter — so a
value crossing the sitk/numpy boundary N times pays the conversion once.

HONEST CONSTRAINTS (do not paper over these):
- sitk -> numpy is zero-copy but read-only (``GetArrayViewFromImage``): a
  kernel that mutates a numpy view obtained this way corrupts the sitk image
  in place (see ``is_readonly_np``). Fused/numba code must write to a fresh
  or pooled buffer, never through ``.np()`` when that flag is set.
- numpy -> sitk always copies (SimpleITK owns its buffers; it cannot wrap a
  foreign one). This is unavoidable, only avoidable to *cross less often* —
  a chain of fused kernels should stay in numpy end-to-end and only pay this
  once, when a legacy sitk kernel finally consumes the result.
- ``nbytes`` sums every resident view's footprint (host + device), because
  the engine's memory accounting (``NodeTable``/admission) must see the true
  resident cost, not just one view of it. A numpy view and the sitk view
  built from it are two independent buffers (the copy above) and both count.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

_sitk = None


def _simpleitk():
    global _sitk
    if _sitk is None:
        import SimpleITK
        _sitk = SimpleITK
    return _sitk


# GetPixelID() -> bytes per scalar component. Built lazily (needs the sitk
# module) so a scalar/vector pixel type never needs GetArrayViewFromImage
# just to learn its itemsize (see PolyArray.nbytes).
_PIXEL_ITEMSIZE: dict[int, int] | None = None


def _sitk_itemsize(image: Any) -> int:
    global _PIXEL_ITEMSIZE
    if _PIXEL_ITEMSIZE is None:
        sitk = _simpleitk()
        _PIXEL_ITEMSIZE = {
            sitk.sitkInt8: 1, sitk.sitkUInt8: 1, sitk.sitkLabelUInt8: 1, sitk.sitkVectorInt8: 1, sitk.sitkVectorUInt8: 1,
            sitk.sitkInt16: 2, sitk.sitkUInt16: 2, sitk.sitkLabelUInt16: 2, sitk.sitkVectorInt16: 2, sitk.sitkVectorUInt16: 2,
            sitk.sitkInt32: 4, sitk.sitkUInt32: 4, sitk.sitkLabelUInt32: 4, sitk.sitkVectorInt32: 4, sitk.sitkVectorUInt32: 4,
            sitk.sitkFloat32: 4, sitk.sitkVectorFloat32: 4, sitk.sitkComplexFloat32: 8,
            sitk.sitkInt64: 8, sitk.sitkUInt64: 8, sitk.sitkLabelUInt64: 8, sitk.sitkVectorInt64: 8, sitk.sitkVectorUInt64: 8,
            sitk.sitkFloat64: 8, sitk.sitkVectorFloat64: 8, sitk.sitkComplexFloat64: 16,
        }
    return _PIXEL_ITEMSIZE.get(image.GetPixelID(), 4)  # unknown type: 4-byte fallback, never crash accounting


def _sitk_nbytes(image: Any) -> int:
    """Byte footprint of a sitk image from pure metadata — no array view built."""
    return image.GetNumberOfPixels() * image.GetNumberOfComponentsPerPixel() * _sitk_itemsize(image)


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
            sitk = _simpleitk()
            arr = sitk.GetArrayViewFromImage(sitk_image)
            self._views["np"] = arr
            self._readonly_np = True
            self.dtype = arr.dtype
            self.shape = tuple(arr.shape)
            return arr

    def sitk(self):
        """SimpleITK image view. Built (copied from the numpy view) on first
        request, then cached."""
        cached = self._views.get("sitk")
        if cached is not None:
            return cached
        with self._view_lock:
            cached = self._views.get("sitk")
            if cached is not None:
                return cached
            sitk = _simpleitk()
            arr = self.np()
            image = sitk.GetImageFromArray(arr, isVector=self.geometry.components > 1)
            image.SetSpacing(self.geometry.spacing)
            image.SetOrigin(self.geometry.origin)
            image.SetDirection(self.geometry.direction)
            self._views["sitk"] = image
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
