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

from dataclasses import dataclass
from typing import Any

_sitk = None


def _simpleitk():
    global _sitk
    if _sitk is None:
        import SimpleITK
        _sitk = SimpleITK
    return _sitk


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

    __slots__ = ("geometry", "dtype", "shape", "_views", "_readonly_np")

    def __init__(self, geometry: Geometry, dtype: Any, shape: tuple[int, ...]):
        self.geometry = geometry
        self.dtype = dtype
        self.shape = shape
        self._views: dict[str, Any] = {}
        # True iff the cached "np" view is a read-only zero-copy alias of a
        # sitk-owned buffer — writing through it would corrupt the source image.
        self._readonly_np = False

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

        Touches ``.np()`` to size a sitk-only value — cheap (zero-copy: it
        just aliases the sitk buffer) and populates a cache other callers
        will reuse anyway. When the numpy view is that alias
        (``is_readonly_np``), sitk and numpy are one buffer and count once.
        When the numpy view was constructed independently and ``.sitk()``
        was then built from it, ``GetImageFromArray`` copied it into a
        second, same-sized, independent buffer — counted again (same byte
        count as the numpy view, since sitk reinterprets the identical data).
        """
        one_buffer = self.np().nbytes
        if "sitk" in self._views and not self._readonly_np:
            return one_buffer * 2
        return one_buffer

    def release_view(self, name: str) -> None:
        """Drop a cached view (e.g. after transferring off a device)."""
        if name == "np" and self._readonly_np:
            self._readonly_np = False
        self._views.pop(name, None)

    def resident_views(self) -> tuple[str, ...]:
        return tuple(self._views.keys())
