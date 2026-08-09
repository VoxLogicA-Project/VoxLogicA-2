"""VoxLogicA experimental-branch compatibility kernels."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import RLock
import os
import math
from typing import Any, SupportsFloat, cast

import numpy as np
import SimpleITK as sitk
try:
    from numba import get_num_threads, njit, prange
    _HAS_NUMBA = True
except Exception:  # pragma: no cover - optional acceleration
    _HAS_NUMBA = False

    def njit(*args, **kwargs):  # type: ignore[misc]
        def _decorator(func):
            return func
        return _decorator

    def prange(*args):  # type: ignore[misc]
        return range(*args)

    def get_num_threads() -> int:  # type: ignore[misc]
        return os.cpu_count() or 1

from voxlogica.arrays import (
    WritableViewUnavailable,
    allocate_writable_like,
    pinned_view,
    writable_sitk_output_mode,
)
from voxlogica.primitives.default._sequence_math import apply_binary_op

# ITK's legacy "Platform" threader spawns and destroys native threads on every
# filter Execute(); over a long run (thousands of distance-map/morphology calls)
# that thread churn intermittently segfaults inside ITK under sustained load
# (captured: SIGSEGV in SignedMaurerDistanceMapImageFilter.Execute via dt). Switch
# to the persistent thread-pool threader, which reuses a fixed pool and is ITK's
# modern default. Done once at import, before any filter runs.
try:
    sitk.ProcessObject.SetGlobalDefaultThreader("Pool")
except Exception as e:  # pragma: no cover - older SimpleITK without the setter
    import sys
    print(f"WARNING: could not set ITK threader to Pool: {e}", file=sys.stderr)

_BASE_IMAGE: sitk.Image | None = None
_BASE_IMAGE_LOCK = RLock()
_CROSSCORR_BACKEND_ENV = "VOXLOGICA_VOX1_CROSSCORR_BACKEND"
_NATIVE_BITWISE_PIXEL_IDS = frozenset({sitk.sitkUInt8})
_NATIVE_COMPARISON_PIXEL_IDS = frozenset({sitk.sitkUInt8, sitk.sitkFloat32})
_NATIVE_MASK_PIXEL_IDS = frozenset({sitk.sitkUInt8, sitk.sitkFloat32})


def _crosscorr_backend() -> str:
    requested = os.environ.get(_CROSSCORR_BACKEND_ENV, "").strip().lower()
    if requested in {"python", "numba", "numpy"}:
        if requested == "numba" and not _HAS_NUMBA:
            return "python"
        return requested
    if _HAS_NUMBA:
        return "numba"
    return "numpy"


def reset_runtime_state() -> None:
    """Reset namespace runtime state before a new execution run."""
    global _BASE_IMAGE
    with _BASE_IMAGE_LOCK:
        _BASE_IMAGE = None
    _snake_cached.cache_clear()
    _hyperrectangle_cached.cache_clear()
    _hyperrectangle_numba_faces_cached.cache_clear()


def _is_image(value: object) -> bool:
    return isinstance(value, sitk.Image)


def _as_image(value: object, arg_name: str) -> sitk.Image:
    if not isinstance(value, sitk.Image):
        raise ValueError(f"{arg_name} must be a SimpleITK Image, got {type(value).__name__}")
    return value


def _remember_base(image: sitk.Image) -> None:
    global _BASE_IMAGE
    with _BASE_IMAGE_LOCK:
        _BASE_IMAGE = image


def _remember_base_from_values(*values: object) -> None:
    for value in values:
        if isinstance(value, sitk.Image):
            _remember_base(value)
            return


def _require_base() -> sitk.Image:
    with _BASE_IMAGE_LOCK:
        if _BASE_IMAGE is None:
            raise ValueError("No model loaded (base image is undefined)")
        return _BASE_IMAGE


def _new_image_like(reference: sitk.Image, pixel_id: int) -> sitk.Image:
    image = sitk.Image(reference.GetSize(), pixel_id)
    image.CopyInformation(reference)
    return image


def _filled_image_like(reference: sitk.Image, pixel_id: int, value: float | int) -> sitk.Image:
    output_pair = _try_native_output(reference, pixel_id)
    if output_pair is not None:
        output, values = output_pair
        values.fill(value)
        return output

    template = _new_image_like(reference, pixel_id)
    shape = sitk.GetArrayViewFromImage(template).shape
    if pixel_id == sitk.sitkUInt8:
        array = np.full(shape, np.uint8(value), dtype=np.uint8)
    else:
        array = np.full(shape, np.float32(value), dtype=np.float32)
    result = sitk.GetImageFromArray(array, isVector=False)
    result.CopyInformation(template)
    return result


def _as_bool_image(image: sitk.Image) -> sitk.Image:
    # Casting to a type the image already has is a full buffer copy for no
    # gain (~10ms on a BraTS volume, ~17000x the cost of this guard).
    if image.GetPixelID() == sitk.sitkUInt8:
        return image
    return sitk.Cast(image, sitk.sitkUInt8)


def _native_images_compatible(*images: sitk.Image) -> bool:
    """Conservative admission gate for direct NumPy output kernels.

    SimpleITK owns validation of unusual geometry/type combinations.  The
    fast path accepts only scalar images with exactly matching metadata; every
    other case takes the established filter path and therefore preserves its
    precise error behavior.
    """
    if not images or any(image.GetNumberOfComponentsPerPixel() != 1 for image in images):
        return False
    reference = images[0]
    return all(
        image.GetDimension() == reference.GetDimension()
        and image.GetSize() == reference.GetSize()
        and image.GetSpacing() == reference.GetSpacing()
        and image.GetOrigin() == reference.GetOrigin()
        and image.GetDirection() == reference.GetDirection()
        for image in images[1:]
    )


def _try_native_output(reference: sitk.Image, pixel_id: int) -> tuple[sitk.Image, np.ndarray] | None:
    """Fresh native output, or ``None`` when this runtime cannot support it."""
    if not _native_images_compatible(reference):
        return None
    try:
        image, view = allocate_writable_like(reference, pixel_id)
    except WritableViewUnavailable:
        if writable_sitk_output_mode() == "required":
            raise
        return None
    return image, view


def _native_comparison(left: object, right: object, op_name: str) -> sitk.Image | None:
    """Direct native-image implementation of one SimpleITK comparison."""
    image_values = tuple(value for value in (left, right) if _is_image(value))
    if not image_values or not _native_images_compatible(*image_values):
        return None
    if any(image.GetPixelID() not in _NATIVE_COMPARISON_PIXEL_IDS for image in image_values):
        return None
    if len(image_values) == 2 and image_values[0].GetPixelID() != image_values[1].GetPixelID():
        return None
    output_pair = _try_native_output(image_values[0], sitk.sitkUInt8)
    if output_pair is None:
        return None
    output, out = output_pair
    operands = (
        pinned_view(left) if _is_image(left) else float(cast(SupportsFloat, left)),
        pinned_view(right) if _is_image(right) else float(cast(SupportsFloat, right)),
    )
    np_ops = {
        "Equal": np.equal,
        "NotEqual": np.not_equal,
        "Less": np.less,
        "LessEqual": np.less_equal,
        "Greater": np.greater,
        "GreaterEqual": np.greater_equal,
    }
    np_ops[op_name](*operands, out=out, casting="unsafe")
    return output


def _as_float_image(image: sitk.Image) -> sitk.Image:
    if image.GetPixelID() == sitk.sitkFloat32:
        return image
    return sitk.Cast(image, sitk.sitkFloat32)


def _flatten_image(image: sitk.Image, dtype: Any = None) -> np.ndarray:
    """Flat view of ``image``'s voxels — zero-copy unless a dtype conversion
    is genuinely required, in which case the conversion IS the copy.

    The result is read-only (it aliases sitk-owned memory); callers that need
    to write must allocate their own buffer.
    """
    view = pinned_view(image).reshape(-1)
    if dtype is None or view.dtype == dtype:
        return view
    return view.astype(dtype)


def _make_image_from_flat(
    flat: np.ndarray,
    shape: tuple[int, ...],
    reference: sitk.Image,
    dtype: Any,
) -> sitk.Image:
    array = np.asarray(flat, dtype=dtype).reshape(shape)
    image = sitk.GetImageFromArray(array, isVector=False)
    image.CopyInformation(reference)
    return image


def num_div(left: float, right: float) -> float:
    """Scalar floating-point division."""
    return float(left) / float(right)


def num_mul(left: float, right: float) -> float:
    """Scalar floating-point multiplication."""
    return float(left) * float(right)


def num_add(left: float, right: float) -> float:
    """Scalar floating-point addition."""
    return float(left) + float(right)


def num_sub(left: float, right: float) -> float:
    """Scalar floating-point subtraction."""
    return float(left) - float(right)


def bool_and_scalar(left: bool, right: bool) -> bool:
    """Scalar boolean and."""
    return bool(left) and bool(right)


def bool_or_scalar(left: bool, right: bool) -> bool:
    """Scalar boolean or."""
    return bool(left) or bool(right)


def bool_not_scalar(value: bool) -> bool:
    """Scalar boolean not."""
    return not bool(value)


def not_compat(value: object) -> object:
    """Boolean not dispatching over scalars and images."""
    if isinstance(value, (bool, int, float)):
        return bool_not_scalar(bool(value))
    return logical_not(value)


def num_eq(left: float, right: float) -> bool:
    """Scalar floating-point equality."""
    return float(left) == float(right)


def num_neq(left: float, right: float) -> bool:
    """Scalar floating-point inequality."""
    return float(left) != float(right)


def num_leq(left: float, right: float) -> bool:
    """Scalar floating-point less-or-equal."""
    return float(left) <= float(right)


def num_lt(left: float, right: float) -> bool:
    """Scalar floating-point less-than."""
    return float(left) < float(right)


def num_geq(left: float, right: float) -> bool:
    """Scalar floating-point greater-or-equal."""
    return float(left) >= float(right)


def num_gt(left: float, right: float) -> bool:
    """Scalar floating-point greater-than."""
    return float(left) > float(right)


def _comparison_values(left: object, right: object, op_name: str) -> object:
    if _is_image(left) and _is_image(right):
        _remember_base_from_values(left, right)
        native = _native_comparison(left, right, op_name)
        if native is not None:
            return native
        return getattr(sitk, op_name)(left, right)
    if _is_image(left):
        _remember_base(left)
        native = _native_comparison(left, right, op_name)
        if native is not None:
            return native
        return getattr(sitk, op_name)(left, float(cast(SupportsFloat, right)))
    if _is_image(right):
        _remember_base(right)
        native = _native_comparison(left, right, op_name)
        if native is not None:
            return native
        flipped = {
            "Equal": "Equal",
            "NotEqual": "NotEqual",
            "Less": "Greater",
            "LessEqual": "GreaterEqual",
            "Greater": "Less",
            "GreaterEqual": "LessEqual",
        }[op_name]
        return getattr(sitk, flipped)(right, float(cast(SupportsFloat, left)))

    left_value = float(cast(SupportsFloat, left))
    right_value = float(cast(SupportsFloat, right))
    if op_name == "Equal":
        return left_value == right_value
    if op_name == "NotEqual":
        return left_value != right_value
    if op_name == "Less":
        return left_value < right_value
    if op_name == "LessEqual":
        return left_value <= right_value
    if op_name == "Greater":
        return left_value > right_value
    if op_name == "GreaterEqual":
        return left_value >= right_value
    raise ValueError(f"Unsupported comparison operator: {op_name}")


def equal(left: object, right: object) -> object:
    """Scalar or voxel-wise equality."""
    return apply_binary_op(
        "Equal",
        left,
        right,
        lambda a, b: _comparison_values(a, b, "Equal"),
    )


def not_equal(left: object, right: object) -> object:
    """Scalar or voxel-wise inequality."""
    return apply_binary_op(
        "NotEqual",
        left,
        right,
        lambda a, b: _comparison_values(a, b, "NotEqual"),
    )


def less(left: object, right: object) -> object:
    """Scalar or voxel-wise less-than."""
    return apply_binary_op(
        "Less",
        left,
        right,
        lambda a, b: _comparison_values(a, b, "Less"),
    )


def less_equal(left: object, right: object) -> object:
    """Scalar or voxel-wise less-or-equal."""
    return apply_binary_op(
        "LessEqual",
        left,
        right,
        lambda a, b: _comparison_values(a, b, "LessEqual"),
    )


def greater(left: object, right: object) -> object:
    """Scalar or voxel-wise greater-than."""
    return apply_binary_op(
        "Greater",
        left,
        right,
        lambda a, b: _comparison_values(a, b, "Greater"),
    )


def greater_equal(left: object, right: object) -> object:
    """Scalar or voxel-wise greater-or-equal."""
    return apply_binary_op(
        "GreaterEqual",
        left,
        right,
        lambda a, b: _comparison_values(a, b, "GreaterEqual"),
    )


def bconstant(value: bool) -> sitk.Image:
    """Boolean constant image filled with a given value."""
    if bool(value):
        return tt()
    return ff()


def tt() -> sitk.Image:
    """Boolean true image."""
    base = _require_base()
    return _filled_image_like(base, sitk.sitkUInt8, 1)


def ff() -> sitk.Image:
    """Boolean false image."""
    base = _require_base()
    return _filled_image_like(base, sitk.sitkUInt8, 0)


def logical_not(image: object) -> sitk.Image:
    """Voxel-wise boolean negation."""
    img = _as_image(image, "image")
    _remember_base(img)
    output_pair = (
        _try_native_output(img, sitk.sitkUInt8)
        if img.GetPixelID() in _NATIVE_BITWISE_PIXEL_IDS
        else None
    )
    if output_pair is not None:
        output, out = output_pair
        # sitk.Not normalizes through ``!= 0``; it is not a raw bitwise NOT.
        np.equal(pinned_view(img), 0, out=out, casting="unsafe")
        return output
    return sitk.Not(img)


def logical_and(left: object, right: object) -> object:
    """Voxel-wise boolean and."""
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        images = tuple(value for value in (left, right) if _is_image(value))
        if images[0].GetPixelID() in _NATIVE_BITWISE_PIXEL_IDS and _native_images_compatible(*images) and (
            len(images) == 1 or images[0].GetPixelID() == images[1].GetPixelID()
        ):
            output_pair = _try_native_output(images[0], images[0].GetPixelID())
            if output_pair is not None:
                output, out = output_pair
                lhs = pinned_view(left) if _is_image(left) else np.uint8(1 if bool(left) else 0)
                rhs = pinned_view(right) if _is_image(right) else np.uint8(1 if bool(right) else 0)
                np.bitwise_and(lhs, rhs, out=out, casting="unsafe")
                return output
        return sitk.And(left, right)
    return bool(left) and bool(right)


def logical_or(left: object, right: object) -> object:
    """Voxel-wise boolean or."""
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        images = tuple(value for value in (left, right) if _is_image(value))
        if images[0].GetPixelID() in _NATIVE_BITWISE_PIXEL_IDS and _native_images_compatible(*images) and (
            len(images) == 1 or images[0].GetPixelID() == images[1].GetPixelID()
        ):
            output_pair = _try_native_output(images[0], images[0].GetPixelID())
            if output_pair is not None:
                output, out = output_pair
                lhs = pinned_view(left) if _is_image(left) else np.uint8(1 if bool(left) else 0)
                rhs = pinned_view(right) if _is_image(right) else np.uint8(1 if bool(right) else 0)
                np.bitwise_or(lhs, rhs, out=out, casting="unsafe")
                return output
        return sitk.Or(left, right)
    return bool(left) or bool(right)


def dt(image: object) -> sitk.Image:
    """Signed Maurer distance transform."""
    img = _as_image(image, "image")
    _remember_base(img)
    flt = sitk.SignedMaurerDistanceMapImageFilter()
    flt.SetInsideIsPositive(False)
    flt.SetSquaredDistance(False)
    flt.SetUseImageSpacing(True)
    flt.SetBackgroundValue(0.0)
    return flt.Execute(_as_bool_image(img))


def gradient(image: object) -> sitk.Image:
    """Gradient magnitude of an image."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.GradientMagnitude(_as_float_image(img))


def constant(value: float) -> sitk.Image:
    """Numeric constant image filled with a given value."""
    base = _require_base()
    return _filled_image_like(base, sitk.sitkFloat32, float(value))


def eq_sv(value: float, image: object) -> sitk.Image:
    """Mask of voxels equal to a scalar value."""
    img = _as_image(image, "image")
    _remember_base(img)
    output_pair = (
        _try_native_output(img, sitk.sitkUInt8)
        if img.GetPixelID() in _NATIVE_COMPARISON_PIXEL_IDS
        else None
    )
    if output_pair is not None:
        output, out = output_pair
        np.equal(pinned_view(img), float(value), out=out, casting="unsafe")
        return output
    return sitk.BinaryThreshold(img, float(value), float(value), 1, 0)


def geq_sv(value: float, image: object) -> sitk.Image:
    """Mask of voxels greater than or equal to a scalar value."""
    img = _as_image(image, "image")
    _remember_base(img)
    output_pair = (
        _try_native_output(img, sitk.sitkUInt8)
        if img.GetPixelID() in _NATIVE_COMPARISON_PIXEL_IDS
        else None
    )
    if output_pair is not None:
        output, out = output_pair
        np.greater_equal(pinned_view(img), float(value), out=out, casting="unsafe")
        return output
    return sitk.GreaterEqual(img, float(value))


def leq_sv(value: float, image: object) -> sitk.Image:
    """Mask of voxels less than or equal to a scalar value."""
    img = _as_image(image, "image")
    _remember_base(img)
    output_pair = (
        _try_native_output(img, sitk.sitkUInt8)
        if img.GetPixelID() in _NATIVE_COMPARISON_PIXEL_IDS
        else None
    )
    if output_pair is not None:
        output, out = output_pair
        np.less_equal(pinned_view(img), float(value), out=out, casting="unsafe")
        return output
    return sitk.LessEqual(img, float(value))


def between(value1: float, value2: float, image: object) -> sitk.Image:
    """Mask of voxels within an inclusive scalar range."""
    img = _as_image(image, "image")
    _remember_base(img)
    output_pair = (
        _try_native_output(img, sitk.sitkUInt8)
        if img.GetPixelID() in _NATIVE_COMPARISON_PIXEL_IDS
        else None
    )
    if output_pair is not None:
        output, out = output_pair
        values = pinned_view(img)
        np.greater_equal(values, float(value1), out=out, casting="unsafe")
        # Retain false elements from the first comparison, then overwrite only
        # its true elements with the upper-bound comparison.
        np.less_equal(values, float(value2), out=out, where=out.astype(bool), casting="unsafe")
        return output
    return sitk.BinaryThreshold(img, float(value1), float(value2), 1, 0)


def max_value(image: object) -> float:
    """Maximum voxel value."""
    img = _as_image(image, "image")
    _remember_base(img)
    flt = sitk.MinimumMaximumImageFilter()
    flt.Execute(img)
    return float(flt.GetMaximum())


def abs_value(image: object) -> sitk.Image:
    """Voxel-wise absolute value."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Abs(img)


def min_value(image: object) -> float:
    """Minimum voxel value."""
    img = _as_image(image, "image")
    _remember_base(img)
    flt = sitk.MinimumMaximumImageFilter()
    flt.Execute(img)
    return float(flt.GetMinimum())


def _add_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Add(left, right)
    return float(cast(SupportsFloat, left)) + float(cast(SupportsFloat, right))


def _mul_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Multiply(left, right)
    return float(cast(SupportsFloat, left)) * float(cast(SupportsFloat, right))


def _div_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Divide(left, right)
    return float(cast(SupportsFloat, left)) / float(cast(SupportsFloat, right))


def _sub_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Subtract(left, right)
    return float(cast(SupportsFloat, left)) - float(cast(SupportsFloat, right))


def add(left: object, right: object) -> object:
    """Voxel-wise or scalar addition."""
    return apply_binary_op("Add", left, right, _add_values)


def multiply(left: object, right: object) -> object:
    """Voxel-wise or scalar multiplication."""
    return apply_binary_op("Multiply", left, right, _mul_values)


def divide(left: object, right: object) -> object:
    """Voxel-wise or scalar division."""
    return apply_binary_op("Division", left, right, _div_values)


def subtract(left: object, right: object) -> object:
    """Voxel-wise or scalar subtraction."""
    return apply_binary_op("Subtraction", left, right, _sub_values)


@njit(cache=True, nogil=True, parallel=True)
def _mask_into(src, msk, dst):
    """``dst[i] = src[i] if msk[i] else 0`` — one pass, no temporaries."""
    for i in prange(src.shape[0]):
        if msk[i] != 0:
            dst[i] = src[i]
        else:
            dst[i] = 0


def mask(image: object, mask_image: object) -> sitk.Image:
    """Zero out voxels where a boolean mask is false."""
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)
    # The legacy kernel first coerces the mask to UInt8.  Keep the direct path
    # to already-UInt8 masks so NumPy's ``!= 0`` condition is exactly the
    # filter's convention without introducing a hidden cast/copy.
    if (
        img.GetPixelID() in _NATIVE_MASK_PIXEL_IDS
        and msk.GetPixelID() == sitk.sitkUInt8
        and _native_images_compatible(img, msk)
    ):
        output_pair = _try_native_output(img, img.GetPixelID())
        if output_pair is not None:
            output, out = output_pair
            # This was `out.fill(0)` then `np.copyto(..., where=mask != 0)`.
            # NumPy's `where=` is not a vectorized select: it walks the buffer
            # under a per-element predicate, which cost 28 ms on a BraTS volume
            # against 0.4-2.9 ms for every alternative — two passes plus a
            # full-size boolean temporary, for a select. A jitted select writes
            # the pooled output in one pass with no temporary at all.
            #
            # `np.multiply(img, mask != 0)` is the obvious vectorized rewrite
            # and is WRONG: NaN*0 is NaN, not 0, so masked-out non-finite
            # voxels would survive. Verified — it differs from the ITK path
            # exactly on NaN and +/-inf.
            if _HAS_NUMBA:
                _mask_into(pinned_view(img).reshape(-1), pinned_view(msk).reshape(-1),
                           out.reshape(-1))
            else:
                np.copyto(out, np.where(pinned_view(msk) != 0, pinned_view(img), 0))
            return output
    return sitk.Mask(img, _as_bool_image(msk), 0.0)


def avg(image: object, mask_image: object) -> float:
    """Mean voxel value inside a boolean mask; raises if the mask is empty."""
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)
    img_values = _flatten_image(_as_float_image(img), np.float32)
    mask_values = _flatten_image(_as_bool_image(msk), np.uint8)
    if img_values.shape[0] != mask_values.shape[0]:
        raise ValueError("avg requires images with the same number of voxels")
    selected = img_values[mask_values > 0]
    if selected.size == 0:
        raise ValueError("avg failed: mask selects no voxels")
    return float(np.mean(selected, dtype=np.float64))


def avg0(image: object, mask_image: object) -> float:
    """Mean value inside a boolean mask, or 0.0 when the mask is empty."""
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)
    img_values = _flatten_image(_as_float_image(img), np.float32)
    mask_values = _flatten_image(_as_bool_image(msk), np.uint8)
    selected = img_values[mask_values > 0]
    if selected.size == 0:
        return 0.0
    return float(np.mean(selected, dtype=np.float64))


def div_sv(value: float, image: object) -> sitk.Image:
    """Scalar divided by each voxel."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Divide(float(value), img)


def sub_sv(value: float, image: object) -> sitk.Image:
    """Scalar minus each voxel."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Subtract(float(value), img)


def div_vs(image: object, value: float) -> sitk.Image:
    """Each voxel divided by a scalar."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Multiply(img, 1.0 / float(value))


def sub_vs(image: object, value: float) -> sitk.Image:
    """Each voxel minus a scalar."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Subtract(img, float(value))


def add_vs(image: object, value: float) -> sitk.Image:
    """Each voxel plus a scalar."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Add(img, float(value))


def mul_vs(image: object, value: float) -> sitk.Image:
    """Each voxel multiplied by a scalar."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Multiply(img, float(value))


@njit(cache=True, nogil=True, parallel=True)
def _dilate_box3_separable(src, mid, dst):
    """3x3x3 binary box dilation as three 1D max passes (z, then y, then x).

    A box structuring element is SEPARABLE: max over the 27-voxel cube equals
    max-over-3 applied along each axis in turn. That is 9 reads per voxel
    instead of 27, three linear passes with perfect locality, and it
    parallelises over slices. ITK's BinaryDilate does not exploit this here:
    measured on a 155x240x240 volume it takes 86 ms, against 1.0 ms for this
    kernel -- 84x, bit-identical on every shape and density tested.

    Boundary: clamping the index replicates the edge voxel, which for a MAX is
    identical to treating the outside as background, matching
    sitk.BinaryDilate(..., sitkBox, 1.0).
    """
    nz, ny, nx = src.shape
    for z in prange(nz):
        zm = z - 1 if z > 0 else 0
        zp = z + 1 if z < nz - 1 else nz - 1
        for y in range(ny):
            for x in range(nx):
                v = src[zm, y, x]
                a = src[z, y, x]
                b = src[zp, y, x]
                if a > v:
                    v = a
                if b > v:
                    v = b
                mid[z, y, x] = v
    for z in prange(nz):
        for y in range(ny):
            ym = y - 1 if y > 0 else 0
            yp = y + 1 if y < ny - 1 else ny - 1
            for x in range(nx):
                v = mid[z, ym, x]
                a = mid[z, y, x]
                b = mid[z, yp, x]
                if a > v:
                    v = a
                if b > v:
                    v = b
                dst[z, y, x] = v
    for z in prange(nz):
        for y in range(ny):
            for x in range(nx):
                xm = x - 1 if x > 0 else 0
                xp = x + 1 if x < nx - 1 else nx - 1
                v = dst[z, y, xm]
                a = dst[z, y, x]
                b = dst[z, y, xp]
                if a > v:
                    v = a
                if b > v:
                    v = b
                mid[z, y, x] = v


def near(image: object) -> sitk.Image:
    """Spatial dilation by one voxel (26-connectivity box kernel)."""
    img = _as_image(image, "image")
    _remember_base(img)
    binary = _as_bool_image(img)
    if _HAS_NUMBA:
        output_pair = _try_native_output(binary, sitk.sitkUInt8)
        if output_pair is not None:
            output, out = output_pair
            src = _flatten_image(binary, np.uint8).reshape(out.shape)
            scratch = np.empty_like(out)
            _dilate_box3_separable(src, out, scratch)
            out[...] = scratch
            return output
    return sitk.BinaryDilate(binary, [1, 1, 1], sitk.sitkBox, 1.0)


def interior(image: object) -> sitk.Image:
    """Spatial erosion by one voxel (26-connectivity box kernel)."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.BinaryErode(_as_bool_image(img), [1, 1, 1], sitk.sitkBox, 0.0)


# ── Run-based connected components (26-connectivity) ────────────────────────
#
# `through` and `maxvol` both used sitk.ConnectedComponent, together 16% of the
# sweep's kernel time (2904 s + 1276 s of 25565 s). ITK labels VOXELS: it walks
# every voxel, unions it with its already-seen neighbours, then writes a uint32
# label volume that the caller immediately reduces away again — 4 bytes per
# voxel of traffic (35 MB on a BraTS volume) for information neither primitive
# actually wants.
#
# These kernels label RUNS instead: each row of constant y,z is compressed to
# its maximal foreground intervals, and union-find operates on intervals, not
# voxels. Two runs are 26-connected exactly when their x-intervals overlap
# after expanding one of them by a voxel on each side, so linking two adjacent
# rows is one linear two-pointer walk. For real masks that is ~100k runs
# against 8.9M voxels, and neither the label volume nor its reduction is ever
# materialized — only a per-component flag array and one write pass.
#
# Measured on a 155x240x240 volume (155*240*240 = 8.9M voxels):
#
#   blobby mask, 20% fg    through 16.1 ms vs 164.6 ms   maxvol 14.5 vs 173.5
#   salt-and-pepper p=0.5  through  108.9 ms vs 926.4 ms   (2.2M runs, worst case)
#   all foreground           4.4 ms vs  13.7 ms
#   empty                    1.1 ms vs   3.1 ms
#
# The adversarial case — uniform noise, where run compression buys least and
# the run count approaches n/4 — is still 8.5x ahead, so there is no density
# at which this path regresses.
#
# Bit-identical to the ITK path on every shape and density tested (blobby and
# uniform-random fills from 0% to 100%, cubic and strongly anisotropic
# volumes, and degenerate extents down to 2x3x4), verified voxel-by-voxel in
# tests/unit/test_vox1_connected_components.py.


@njit(cache=True, nogil=True, parallel=True)
def _cc_count_runs(fg, counts):
    """Number of maximal foreground runs in each row (one row per y,z)."""
    nz, ny, nx = fg.shape
    for r in prange(nz * ny):
        z = r // ny
        y = r - z * ny
        c = 0
        prev = np.uint8(0)
        for x in range(nx):
            v = fg[z, y, x]
            if v != 0 and prev == 0:
                c += 1
            prev = v
        counts[r] = c


@njit(cache=True, nogil=True, parallel=True)
def _cc_fill_runs(fg, offsets, starts, ends):
    """Write each row's runs (inclusive x bounds) into its slice of the arrays."""
    nz, ny, nx = fg.shape
    for r in prange(nz * ny):
        z = r // ny
        y = r - z * ny
        k = offsets[r]
        x = 0
        while x < nx:
            if fg[z, y, x] != 0:
                s = x
                while x < nx and fg[z, y, x] != 0:
                    x += 1
                starts[k] = s
                ends[k] = x - 1
                k += 1
            else:
                x += 1


@njit(cache=True, nogil=True, inline="always")
def _cc_find(parent, i):
    root = i
    while parent[root] != root:
        root = parent[root]
    while parent[i] != root:  # path compression
        nxt = parent[i]
        parent[i] = root
        i = nxt
    return root


@njit(cache=True, nogil=True, inline="always")
def _cc_union(parent, a, b):
    ra = _cc_find(parent, a)
    rb = _cc_find(parent, b)
    if ra < rb:
        parent[rb] = ra
    elif rb < ra:
        parent[ra] = rb


@njit(cache=True, nogil=True)
def _cc_link_rows(offsets, starts, ends, parent, ra, rb):
    """Union every 26-connected pair of runs across two rows.

    Both rows' runs are sorted and disjoint, so one two-pointer walk sees every
    overlapping pair: advance whichever run ends first. Runs [s1,e1] and
    [s2,e2] in diagonally- or orthogonally-adjacent rows touch under
    26-connectivity iff their x-intervals overlap once either is grown by one
    voxel at each end, i.e. s1 <= e2+1 and s2 <= e1+1.
    """
    i = offsets[ra]
    iend = offsets[ra + 1]
    j = offsets[rb]
    jend = offsets[rb + 1]
    while i < iend and j < jend:
        if starts[i] <= ends[j] + 1 and starts[j] <= ends[i] + 1:
            _cc_union(parent, i, j)
        if ends[i] < ends[j]:
            i += 1
        else:
            j += 1


@njit(cache=True, nogil=True)
def _cc_union_all(nz, ny, offsets, starts, ends, parent):
    """Union runs into components, visiting rows in raster order.

    Only the four already-visited neighbour rows need linking — (z,y-1),
    (z-1,y-1), (z-1,y) and (z-1,y+1) — because connectivity is symmetric and
    every later row will link back to this one when its own turn comes.
    """
    for i in range(parent.shape[0]):
        parent[i] = i
    for z in range(nz):
        for y in range(ny):
            r = z * ny + y
            if offsets[r] == offsets[r + 1]:
                continue
            if y > 0:
                _cc_link_rows(offsets, starts, ends, parent, r, r - 1)
            if z > 0:
                base = (z - 1) * ny
                _cc_link_rows(offsets, starts, ends, parent, r, base + y)
                if y > 0:
                    _cc_link_rows(offsets, starts, ends, parent, r, base + y - 1)
                if y < ny - 1:
                    _cc_link_rows(offsets, starts, ends, parent, r, base + y + 1)


@njit(cache=True, nogil=True)
def _cc_seeded_flags(ny, offsets, starts, ends, parent, seed):
    """Mark every component holding at least one nonzero ``seed`` voxel."""
    flags = np.zeros(parent.shape[0], dtype=np.uint8)
    for r in range(offsets.shape[0] - 1):
        z = r // ny
        y = r - z * ny
        for k in range(offsets[r], offsets[r + 1]):
            for x in range(starts[k], ends[k] + 1):
                if seed[z, y, x] != 0:
                    flags[_cc_find(parent, k)] = np.uint8(1)
                    break
    return flags


@njit(cache=True, nogil=True)
def _cc_component_volumes(offsets, starts, ends, parent):
    """Voxel count per component, indexed by root run (non-roots stay 0)."""
    volumes = np.zeros(parent.shape[0], dtype=np.int64)
    for k in range(parent.shape[0]):
        volumes[_cc_find(parent, k)] += ends[k] - starts[k] + 1
    return volumes


@njit(cache=True, nogil=True, parallel=True)
def _cc_write_selected(out, ny, offsets, starts, ends, parent, selected):
    """Write 1 over every run whose component is selected; 0 elsewhere."""
    for r in prange(offsets.shape[0] - 1):
        z = r // ny
        y = r - z * ny
        for x in range(out.shape[2]):
            out[z, y, x] = np.uint8(0)
        for k in range(offsets[r], offsets[r + 1]):
            if selected[_cc_find(parent, k)] != 0:
                for x in range(starts[k], ends[k] + 1):
                    out[z, y, x] = np.uint8(1)


def _cc_build_runs(fg: np.ndarray):
    """Run decomposition plus union-find parents for a 3D uint8 foreground."""
    nz, ny, _nx = fg.shape
    counts = np.empty(nz * ny, dtype=np.int64)
    _cc_count_runs(fg, counts)
    offsets = np.empty(nz * ny + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(counts, out=offsets[1:])
    run_count = int(offsets[-1])
    starts = np.empty(run_count, dtype=np.int32)
    ends = np.empty(run_count, dtype=np.int32)
    _cc_fill_runs(fg, offsets, starts, ends)
    parent = np.empty(run_count, dtype=np.int64)
    _cc_union_all(nz, ny, offsets, starts, ends, parent)
    return offsets, starts, ends, parent


def _as_zyx(view: np.ndarray) -> np.ndarray | None:
    """``view`` as a 3D (z,y,x) array, or None if it cannot be one.

    A 2D image becomes a single slice, under which 26-connectivity degenerates
    to exactly the 8-connectivity ITK applies to a 2D image — so the fast path
    stays bit-identical there too. Anything above three dimensions falls back.
    """
    if view.ndim == 3:
        return view
    if view.ndim < 3:
        return view.reshape((1,) * (3 - view.ndim) + view.shape)
    return None


def _label_connected_components(image: sitk.Image) -> tuple[sitk.Image, int]:
    labels = sitk.ConnectedComponent(_as_bool_image(image), True)
    labels_array = _flatten_image(labels, np.uint32)
    max_label = int(labels_array.max(initial=0))
    return labels, max_label


def lcc(image: object) -> sitk.Image:
    """Connected-component label image (float32)."""
    img = _as_image(image, "image")
    _remember_base(img)
    labels, _ = _label_connected_components(img)
    return _as_float_image(labels)


def Lcc(image: object) -> sitk.Image:
    """Alias for lcc."""
    return lcc(image)


@njit(cache=True, nogil=True)
def _through_mask_components_into_numba(
    mask_values: np.ndarray,
    cc_values: np.ndarray,
    max_label: int,
    result: np.ndarray,
) -> None:
    flags = np.zeros(max_label + 1, dtype=np.uint8)
    n = cc_values.shape[0]
    for i in range(n):
        cc = cc_values[i]
        if mask_values[i] != 0 and cc > 0 and cc <= max_label:
            flags[cc] = np.uint8(1)
    for i in range(n):
        cc = cc_values[i]
        if cc > 0 and cc <= max_label:
            result[i] = flags[cc]
        else:
            result[i] = np.uint8(0)


def through(image1: object, image2: object) -> sitk.Image:
    """Mask of components of image2 that intersect image1."""
    img1 = _as_image(image1, "image1")
    img2 = _as_image(image2, "image2")
    _remember_base(img1)

    # `image1` selects seeds by "is nonzero". Below, the uint8 case reads the
    # raw voxels and the general case reads _as_bool_image's cast — mirroring
    # exactly what the two ITK branches at the bottom of this function do, so
    # the fast path inherits their (differing) treatment of, say, an int16 256.
    seed_image = img1 if img1.GetPixelID() == sitk.sitkUInt8 else _as_bool_image(img1)
    binary2 = _as_bool_image(img2)
    if _HAS_NUMBA and _native_images_compatible(seed_image, binary2):
        fg = _as_zyx(pinned_view(binary2))
        seed = _as_zyx(pinned_view(seed_image))
        if fg is not None and seed is not None:
            output_pair = _try_native_output(binary2, sitk.sitkUInt8)
            if output_pair is not None:
                output, out = output_pair
                offsets, starts, ends, parent = _cc_build_runs(fg)
                flags = _cc_seeded_flags(fg.shape[1], offsets, starts, ends, parent, seed)
                _cc_write_selected(out.reshape(fg.shape), fg.shape[1],
                                   offsets, starts, ends, parent, flags)
                return output

    cc_image, max_label = _label_connected_components(img2)
    cc_values = _flatten_image(cc_image, np.uint32)
    if img1.GetPixelID() == sitk.sitkUInt8 and _native_images_compatible(img1, cc_image):
        output_pair = _try_native_output(cc_image, sitk.sitkUInt8)
        if output_pair is not None:
            output, out = output_pair
            mask_values = _flatten_image(img1, np.uint8)
            result_values = out.reshape(-1)
            if _HAS_NUMBA:
                _through_mask_components_into_numba(
                    np.asarray(mask_values, dtype=np.uint8),
                    np.asarray(cc_values, dtype=np.uint32),
                    max_label,
                    result_values,
                )
            else:
                flags = np.zeros(max_label + 1, dtype=np.uint8)
                selected = (mask_values != 0) & (cc_values > 0)
                flags[cc_values[selected]] = 1
                result_values.fill(0)
                non_background = cc_values > 0
                result_values[non_background] = flags[cc_values[non_background]]
            return output

    masked = sitk.Mask(cc_image, _as_bool_image(img1), 0.0)
    masked_values = _flatten_image(masked, np.uint32)
    result_values = np.zeros_like(cc_values, dtype=np.uint8)
    if _HAS_NUMBA:
        _through_mask_components_into_numba(
            np.asarray(masked_values, dtype=np.uint32),
            np.asarray(cc_values, dtype=np.uint32),
            max_label,
            result_values,
        )
    else:
        flags = np.zeros(max_label + 1, dtype=np.uint8)
        active = masked_values[masked_values > 0]
        if active.size > 0:
            flags[active] = 1
        non_background = cc_values > 0
        result_values[non_background] = flags[cc_values[non_background]]

    shape = sitk.GetArrayViewFromImage(cc_image).shape
    return _make_image_from_flat(result_values, shape, cc_image, np.uint8)


def volume(image: object) -> float:
    """Number of true (nonzero) voxels."""
    img = _as_image(image, "image")
    _remember_base(img)
    if img.GetPixelID() == sitk.sitkUInt8:
        # uint8 is unsigned, so (v > 0) is exactly (v != 0), which is the
        # predicate count_nonzero already applies. Going through the general
        # path below would allocate an 8.9M-voxel bool temporary to compute a
        # mask that is then immediately reduced to a single integer.
        return float(np.count_nonzero(pinned_view(img)))
    values = _flatten_image(_as_bool_image(img), np.uint8)
    return float(np.count_nonzero(values > 0))


def vol(image: object) -> float:
    """Alias for volume."""
    return volume(image)


def _as_index_vector(value: object, name: str, dims: int = 3) -> list[int]:
    if isinstance(value, (list, tuple)):
        items = value
    elif hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
    else:
        raise ValueError(f"{name} must be a {dims}-element index vector")
    if len(items) != dims:
        raise ValueError(f"{name} must have length {dims}, got {len(items)}")
    return [int(float(item)) for item in items]


def extract(image: object, size: object, index: object) -> sitk.Image:
    """Extract a subregion from an image given size and origin index vectors."""
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Extract(
        img,
        _as_index_vector(size, "size"),
        _as_index_vector(index, "index"),
    )


def slic(image: object, grid_spacing: float, spatial_weight: float) -> sitk.Image:
    """SLIC superpixels/supervoxels with an integer grid spacing (voxels per cell
    edge). Accepts a scalar or a multi-channel (vector) image -- compose modalities
    with rgb/rgba for multimodal superpixels. Returns an integer label image; higher
    grid_spacing -> fewer, larger regions. Wraps sitk.SLIC so the grid vector is cast
    to the uint type SimpleITK requires (ImgQL numbers are floats)."""
    img = _as_image(image, "image")
    _remember_base(img)
    g = max(1, int(round(float(grid_spacing))))
    dim = img.GetDimension()
    return sitk.SLIC(img, [g] * dim, float(spatial_weight), 10, True, True)


def label_mean(label_image: object, value_image: object) -> sitk.Image:
    """Region-average quotient: paint every label of `label_image` with the mean
    of `value_image` over that label. Turns an over-segmentation (SLIC superpixels,
    watershed basins, percentile-band components) into a piecewise-constant
    homogeneity quotient -- a computable proxy for a bisimilarity quotient on which
    spatial-logic thresholds select whole regions rather than single voxels."""
    lab = _as_image(label_image, "label_image")
    val = _as_image(value_image, "value_image")
    _remember_base(val)
    lab_values = _flatten_image(lab, np.int64)
    val_values = _flatten_image(_as_float_image(val), np.float64)
    if lab_values.shape[0] != val_values.shape[0]:
        raise ValueError("label_mean requires images with the same number of voxels")
    n = int(lab_values.max(initial=0)) + 1
    sums = np.bincount(lab_values, weights=val_values, minlength=n)
    counts = np.bincount(lab_values, minlength=n)
    means = np.zeros(n, dtype=np.float64)
    nonzero = counts > 0
    means[nonzero] = sums[nonzero] / counts[nonzero]
    out = means[lab_values].astype(np.float32)
    shape = sitk.GetArrayViewFromImage(lab).shape
    return _make_image_from_flat(out, shape, lab, np.float32)


def otsu(image: object, mask_image: object, nbins: float) -> sitk.Image:
    """Otsu threshold mask within a given mask region."""
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)
    flt = sitk.OtsuThresholdImageFilter()
    flt.SetInsideValue(0)
    flt.SetOutsideValue(1)
    flt.SetNumberOfHistogramBins(int(nbins))
    flt.SetMaskOutput(True)
    flt.SetMaskValue(1)
    return flt.Execute(img, _as_bool_image(msk))


def n4(image: object, mask_image: object) -> sitk.Image:
    """N4 bias-field correction within a mask (per-image, deterministic; NO learning).

    Removes the low-frequency multiplicative intensity bias (coil/shim inhomogeneity) so a
    single intensity threshold behaves uniformly across the field of view. The bias field is
    estimated on a 2x-shrunk image for speed (it is low-frequency by construction), then the
    full-resolution log-bias is divided out of the original. Returns the corrected intensity
    image (float32); feed it to `percentiles` exactly like a raw channel.
    """
    img = _as_float_image(_as_image(image, "image"))
    msk = _as_bool_image(_as_image(mask_image, "mask_image"))
    _remember_base(img)
    dim = img.GetDimension()
    img_s = sitk.Shrink(img, [2] * dim)
    msk_s = sitk.Shrink(msk, [2] * dim)
    corrector = sitk.N4BiasFieldCorrectionImageFilter()
    corrector.SetMaximumNumberOfIterations([30, 20, 10])
    corrector.Execute(img_s, msk_s)                    # estimate on the shrunk image
    log_bias = corrector.GetLogBiasFieldAsImage(img)   # evaluate at full resolution
    return img / sitk.Exp(log_bias)


def contralateral_asymmetry(image: object, sigma_mm: float) -> sitk.Image:
    """Positive left-right intensity asymmetry after optional Gaussian smoothing.

    The input is assumed to be in a common left-right atlas orientation.  The
    image is reflected across its x-index axis and subtracted from itself; only
    positive residuals remain.  This gives a deterministic, image-derived field
    on which a symbolic region selector can find unilateral abnormalities that
    are dim in absolute intensity but distinct from contralateral tissue.
    """
    sigma = float(sigma_mm)
    if sigma < 0:
        raise ValueError("contralateral_asymmetry requires sigma_mm >= 0")
    img = _as_float_image(_as_image(image, "image"))
    _remember_base(img)
    smoothed = img if sigma == 0 else sitk.SmoothingRecursiveGaussian(img, sigma)
    values = _flatten_image(smoothed, np.float32)
    shape = sitk.GetArrayViewFromImage(smoothed).shape
    array = values.reshape(shape)
    positive = np.maximum(array - np.flip(array, axis=-1), 0.0)
    return _make_image_from_flat(positive.reshape(-1), shape, img, np.float32)


def _surface_distances(a_obj: object, b_obj: object):
    """Symmetric surface distances (mm, image spacing) between two boolean masks.

    Returns (d_pred->ref, d_ref->pred) arrays of per-surface-voxel nearest distances, or
    None if either mask is empty (metric undefined). Used by hd95 and nsd below.
    """
    A = _as_bool_image(_as_image(a_obj, "a"))
    B = _as_bool_image(_as_image(b_obj, "b"))
    # GetArrayView + astype/abs is ONE copy (the conversion itself); the old
    # GetArrayFromImage + astype was two. Each view here is consumed within
    # the expression that builds it, so its source image is still referenced.
    an = sitk.GetArrayViewFromImage(A).astype(bool)
    bn = sitk.GetArrayViewFromImage(B).astype(bool)
    if not an.any() or not bn.any():
        return None
    dist_to_a = np.abs(sitk.GetArrayViewFromImage(
        sitk.SignedMaurerDistanceMap(A, squaredDistance=False, useImageSpacing=True)))
    dist_to_b = np.abs(sitk.GetArrayViewFromImage(
        sitk.SignedMaurerDistanceMap(B, squaredDistance=False, useImageSpacing=True)))
    a_surf = an & ~sitk.GetArrayViewFromImage(sitk.BinaryErode(A, [1, 1, 1])).astype(bool)
    b_surf = bn & ~sitk.GetArrayViewFromImage(sitk.BinaryErode(B, [1, 1, 1])).astype(bool)
    return dist_to_b[a_surf], dist_to_a[b_surf]


def hd95(prediction: object, reference: object) -> float:
    """95th-percentile (robust) Hausdorff surface distance in mm (lower = better).

    A boundary metric that, unlike Dice, is sensitive to localized boundary errors on large
    objects (Metrics Reloaded). Returns -1.0 if either mask is empty (undefined)."""
    sd = _surface_distances(prediction, reference)
    if sd is None:
        return -1.0
    return float(np.percentile(np.concatenate(sd), 95))


def nsd(prediction: object, reference: object, tolerance_mm: float) -> float:
    """Normalized Surface Dice at tolerance tau mm (higher = better, 1 = perfect boundary).

    Fraction of both surfaces lying within tau of the other surface — the boundary analogue
    of Dice, robust to clinically-irrelevant sub-tau deviations. -1.0 if either mask empty."""
    sd = _surface_distances(prediction, reference)
    if sd is None:
        return -1.0
    d_ab, d_ba = sd
    tau = float(tolerance_mm)
    total = len(d_ab) + len(d_ba)
    if total == 0:
        return -1.0
    return float(int((d_ab <= tau).sum() + (d_ba <= tau).sum()) / total)


def maxvol(image: object) -> sitk.Image:
    """Largest connected component mask (ties keep union)."""
    img = _as_image(image, "image")
    _remember_base(img)

    binary = _as_bool_image(img)
    if _HAS_NUMBA:
        fg = _as_zyx(pinned_view(binary))
        if fg is not None:
            output_pair = _try_native_output(binary, sitk.sitkUInt8)
            if output_pair is not None:
                output, out = output_pair
                offsets, starts, ends, parent = _cc_build_runs(fg)
                volumes = _cc_component_volumes(offsets, starts, ends, parent)
                best = int(volumes.max(initial=0))
                # Ties keep the union, matching the ITK path's `volumes == best`.
                selected = (volumes == best).astype(np.uint8) if best > 0 \
                    else np.zeros(volumes.shape, dtype=np.uint8)
                _cc_write_selected(out.reshape(fg.shape), fg.shape[1],
                                   offsets, starts, ends, parent, selected)
                return output

    labels_image, max_label = _label_connected_components(img)
    labels = _flatten_image(labels_image, np.uint32)

    if max_label <= 0:
        selected = np.zeros(1, dtype=np.uint8)
    else:
        volumes = np.bincount(labels, minlength=max_label + 1)
        best = int(volumes[1:].max(initial=0))
        selected = np.zeros(max_label + 1, dtype=np.uint8)
        if best > 0:
            selected[1:] = (volumes[1:] == best).astype(np.uint8)

    result = selected[labels]
    shape = sitk.GetArrayViewFromImage(labels_image).shape
    return _make_image_from_flat(result, shape, labels_image, np.uint8)


# ── Parallel sort for large populations ─────────────────────────────────────
#
# percentiles() (below) is dominated by ONE step: np.argsort over the
# whole population (measured: ~93% of percentiles' wall time on a BraTS-size
# case — see doc/dev/dynamic-scheduler/frontier-scheduler.md). Unlike the
# elementwise fusion work in engine/numba_fusion.py, this is NOT
# memory-bandwidth-bound on a single pass — sorting has real computational
# density and DOES scale with cores (measured: ~4x with 4 threads on 8.9M
# elements). The fix: split the population into chunks, argsort each chunk
# in its own thread (np.argsort releases the GIL), then merge the sorted
# chunks back into one globally sorted sequence.
#
# CORRECTNESS: the merge only needs to produce a validly SORTED sequence —
# tie order among equal values doesn't matter, because the grouping step
# below assigns the SAME output value to an entire run of equal values
# regardless of which physical voxel index appears first within that run.
# So a merge that doesn't replicate np.argsort's exact tie-breaking is still
# bit-identical in RESULT (only which physical index came "first" among
# ties differs internally, and nothing downstream observes that ordering).
#
# _merge_sorted_pairs is the vectorized two-array merge trick: for value v
# with counts t1 in `values1`/t2 in `values2`, searchsorted(...,'right') on
# the other array's count-up-to-and-including plus a 'left' pairing places
# v's t1+t2 occurrences into t1+t2 CONSECUTIVE, non-overlapping output slots
# (proof: pos2's range for v is [L1+L2, L1+L2+t2-1], pos1's is
# [L1+L2+t2, L1+L2+t2+t1-1] where L1/L2 are counts strictly less than v in
# each array — adjacent, no gap, no overlap). O(n log m) vectorized C calls,
# not a Python loop, so merging is cheap even for millions of elements.

def _warm_numba_dispatchers() -> None:
    """Compile the percentile JIT path ONCE, on the importing thread.

    numba's dispatcher is not safe to enter concurrently while it still has to
    produce a specialization, and this build of CPython is free-threaded, so
    nothing else serializes it either. The engine's very first phase calls
    `percentiles` on ~16 cases at once, which is exactly 16 threads racing into
    an uncompiled dispatcher. That corrupted the interpreter three different
    ways in one session on the 369-case sweep: a SIGSEGV, a glibc
    "double free or corruption", and finally

        SystemError: CPUDispatcher(_extract_population) returned a result with
        an exception set
        SystemError: Objects/tupleobject.c:123: bad argument to internal function

    Touching each entry point here with production dtypes forces the
    specialization while only one thread exists. With cache=True the machine
    code is already on disk, so this costs milliseconds, and it CANNOT be done
    by wrapping the dispatchers instead: two of them are inline="always" and are
    called from inside other jitted functions, where a Python wrapper would not
    compile.
    """
    if not _HAS_NUMBA:
        return
    try:
        img = np.zeros(4, dtype=np.float32)
        mask = np.ones(4, dtype=np.uint8)
        population, values = _extract_population(img, mask)
        order = np.argsort(values)
        _group_and_write(values[order].astype(np.float32),
                         population[order].astype(np.int64), 4, 0.0)
        _merge_sorted_pairs(values[:2].astype(np.float32), population[:2].astype(np.int64),
                            values[2:].astype(np.float32), population[2:].astype(np.int64))
        # `near`'s separable dilation: same hazard, and it is called on many
        # cases at once in the same opening phase.
        cube = np.zeros((2, 2, 2), dtype=np.uint8)
        _dilate_box3_separable(cube, np.empty_like(cube), np.empty_like(cube))
        # The run-based connected-component kernels behind `through`/`maxvol`:
        # same hazard again, and `through` is likewise dispatched across many
        # cases concurrently.
        fg = np.array([[[1, 0], [1, 1]], [[0, 1], [0, 0]]], dtype=np.uint8)
        offsets, starts, ends, parent = _cc_build_runs(fg)
        _cc_seeded_flags(fg.shape[1], offsets, starts, ends, parent, fg)
        volumes = _cc_component_volumes(offsets, starts, ends, parent)
        _cc_write_selected(np.zeros_like(fg), fg.shape[1], offsets, starts, ends,
                           parent, (volumes > 0).astype(np.uint8))
        # `mask`'s jitted select, for both pixel types it accepts.
        flat_mask = np.ones(4, dtype=np.uint8)
        for probe_dtype in (np.float32, np.uint8):
            probe = np.zeros(4, dtype=probe_dtype)
            _mask_into(probe, flat_mask, np.empty_like(probe))
    except Exception:  # noqa: BLE001 — a warm-up failure must not stop the run
        pass


_PARALLEL_SORT_MIN_POPULATION = 200_000  # below this, thread/merge overhead isn't worth it
_PARALLEL_SORT_CHUNKS = min(os.cpu_count() or 4, 8)


@njit(cache=True, nogil=True)
def _extract_population(img_values: np.ndarray, mask_values: np.ndarray):
    """(population flat-indices, population values) where mask_values > 0."""
    population_size = 0
    for i in range(mask_values.shape[0]):
        if mask_values[i] > 0:
            population_size += 1
    population = np.empty(population_size, dtype=np.int64)
    population_values = np.empty(population_size, dtype=np.float32)
    cursor = 0
    for i in range(mask_values.shape[0]):
        if mask_values[i] > 0:
            population[cursor] = i
            population_values[cursor] = img_values[i]
            cursor += 1
    return population, population_values


@njit(cache=True, nogil=True)
def _group_and_write(
    sorted_values: np.ndarray,
    sorted_indices: np.ndarray,
    total_voxels: int,
    correction: float,
) -> np.ndarray:
    """Tie-grouped percentile rank, given an ALREADY fully-sorted population
    (any tie order — see module notes above). Shared by both the plain and
    parallel-sort paths in ``percentiles()`` so the grouping algorithm is
    never duplicated."""
    result_values = np.full(total_voxels, np.float32(-1.0), dtype=np.float32)
    vol = float(sorted_values.shape[0])
    if vol == 0.0:
        return result_values
    curvol = 0.0
    group_start = 0
    while group_start < sorted_values.shape[0]:
        group_end = group_start + 1
        while group_end < sorted_values.shape[0] and sorted_values[group_end] == sorted_values[group_start]:
            group_end += 1
        group_size = group_end - group_start
        value = (curvol + (correction * float(group_size))) / vol
        value32 = np.float32(value)
        for idx in range(group_start, group_end):
            result_values[sorted_indices[idx]] = value32
        curvol += float(group_size)
        group_start = group_end
    return result_values


@njit(cache=True, nogil=True)
def _merge_sorted_pairs(values1: np.ndarray, idx1: np.ndarray,
                         values2: np.ndarray, idx2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Merge two ALREADY-sorted (values, original-index) sequences into one
    sorted sequence — the standard two-pointer merge, O(n1+n2). (An earlier
    version used a vectorized ``np.searchsorted`` trick instead of a loop;
    profiling showed that degenerates to O(n log m) for two comparably-sized
    arrays — the SAME order as sorting — which showed up as the merge step
    now dominating wall time instead of the sort. This numba loop is
    genuinely linear.) See the correctness note above the parallel-sort
    section for why tie order across the two inputs is irrelevant here."""
    n1, n2 = values1.shape[0], values2.shape[0]
    merged_values = np.empty(n1 + n2, dtype=values1.dtype)
    merged_idx = np.empty(n1 + n2, dtype=idx1.dtype)
    i, j, k = 0, 0, 0
    while i < n1 and j < n2:
        if values1[i] <= values2[j]:
            merged_values[k] = values1[i]
            merged_idx[k] = idx1[i]
            i += 1
        else:
            merged_values[k] = values2[j]
            merged_idx[k] = idx2[j]
            j += 1
        k += 1
    while i < n1:
        merged_values[k] = values1[i]
        merged_idx[k] = idx1[i]
        i += 1
        k += 1
    while j < n2:
        merged_values[k] = values2[j]
        merged_idx[k] = idx2[j]
        j += 1
        k += 1
    return merged_values, merged_idx


def _parallel_sorted_population(population: np.ndarray, population_values: np.ndarray,
                                 n_chunks: int) -> tuple[np.ndarray, np.ndarray]:
    """(sorted_values, sorted_indices) for the whole population, sorted by
    argsort-ing ``n_chunks`` contiguous chunks in parallel threads (each
    ``np.argsort`` releases the GIL — real parallelism, not cooperative
    scheduling), then merging the sorted chunks back together in a BALANCED
    binary tree (O(N log2 K) total merge work, vs. O(N*K) for a linear fold
    over K chunks — matters here since K can be up to 8)."""
    if population_values.shape[0] == 0:
        return population_values, population
    chunk_bounds = np.linspace(0, population_values.shape[0], n_chunks + 1, dtype=np.int64)

    def _sort_chunk(k: int) -> tuple[np.ndarray, np.ndarray]:
        lo, hi = chunk_bounds[k], chunk_bounds[k + 1]
        chunk_values = population_values[lo:hi]
        order = np.argsort(chunk_values)
        return chunk_values[order], population[lo:hi][order]

    real_chunks = [k for k in range(n_chunks) if chunk_bounds[k] < chunk_bounds[k + 1]]
    with ThreadPoolExecutor(max_workers=len(real_chunks)) as pool:
        sequences = list(pool.map(_sort_chunk, real_chunks))

    while len(sequences) > 1:
        next_round = [
            _merge_sorted_pairs(*sequences[i], *sequences[i + 1])
            for i in range(0, len(sequences) - 1, 2)
        ]
        if len(sequences) % 2 == 1:
            next_round.append(sequences[-1])
        sequences = next_round
    return sequences[0]


_warm_numba_dispatchers()   # single-threaded, before any worker exists


def percentiles(image: object, mask_image: object, correction: float) -> sitk.Image:
    """Percentile rank image within a mask, with tie-breaking correction factor."""
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)

    img_values = _flatten_image(_as_float_image(img), np.float32)
    mask_values = _flatten_image(_as_bool_image(msk), np.uint8)
    if img_values.shape[0] != mask_values.shape[0]:
        raise ValueError("percentiles requires images with the same number of voxels")

    if _HAS_NUMBA:
        img_arr = np.asarray(img_values, dtype=np.float32)
        mask_arr = np.asarray(mask_values, dtype=np.uint8)
        population, population_values = _extract_population(img_arr, mask_arr)
        if population.shape[0] >= _PARALLEL_SORT_MIN_POPULATION and _PARALLEL_SORT_CHUNKS > 1:
            sorted_values, sorted_indices = _parallel_sorted_population(
                population, population_values, _PARALLEL_SORT_CHUNKS)
        else:
            order = np.argsort(population_values)
            sorted_values, sorted_indices = population_values[order], population[order]
        result_values = _group_and_write(
            sorted_values, sorted_indices, img_arr.shape[0], float(correction))
    else:
        result_values = np.full(img_values.shape, -1.0, dtype=np.float32)
        population = np.flatnonzero(mask_values > 0)
        if population.size > 0:
            population_values = img_values[population]
            sorted_order = np.argsort(population_values, kind="mergesort")
            sorted_indices = population[sorted_order]
            sorted_values = population_values[sorted_order]
            vol = float(population.size)
            curvol = 0
            group_start = 0
            while group_start < sorted_values.size:
                group_end = group_start + 1
                while (
                    group_end < sorted_values.size
                    and sorted_values[group_end] == sorted_values[group_start]
                ):
                    group_end += 1
                group_size = group_end - group_start
                value = ((float(curvol)) + (float(correction) * float(group_size))) / vol
                result_values[sorted_indices[group_start:group_end]] = np.float32(value)
                curvol += group_size
                group_start = group_end

    shape = sitk.GetArrayViewFromImage(_as_float_image(img)).shape
    return _make_image_from_flat(result_values, shape, img, np.float32)


def intensity(model: object) -> sitk.Image:
    """Grayscale intensity: ITU-R BT.709 luminance for RGB, passthrough for single-channel."""
    img = _as_image(model, "model")
    _remember_base(img)

    if img.GetNumberOfComponentsPerPixel() == 1:
        return _as_float_image(img)

    red_channel = sitk.VectorIndexSelectionCast(img, 0)
    green_channel = sitk.VectorIndexSelectionCast(img, 1)
    blue_channel = sitk.VectorIndexSelectionCast(img, 2)
    return sitk.Add(
        sitk.Multiply(0.2126, red_channel),
        sitk.Add(
            sitk.Multiply(0.7152, green_channel),
            sitk.Multiply(0.0722, blue_channel),
        ),
    )


def _component(model: object, index: int) -> sitk.Image:
    img = _as_image(model, "model")
    _remember_base(img)
    return _as_float_image(sitk.VectorIndexSelectionCast(img, int(index)))


def red(model: object) -> sitk.Image:
    """Red channel of an RGB or RGBA image."""
    return _component(model, 0)


def green(model: object) -> sitk.Image:
    """Green channel of an RGB or RGBA image."""
    return _component(model, 1)


def blue(model: object) -> sitk.Image:
    """Blue channel of an RGB or RGBA image."""
    return _component(model, 2)


def alpha(model: object) -> sitk.Image:
    """Alpha channel; returns 255 for images without an alpha component."""
    img = _as_image(model, "model")
    _remember_base(img)
    if img.GetNumberOfComponentsPerPixel() < 4:
        return _filled_image_like(img, sitk.sitkFloat32, 255.0)
    return _component(img, 3)


def rgb(red_image: object, green_image: object, blue_image: object) -> sitk.Image:
    """Compose three float images into an RGB vector image."""
    red_img = _as_image(red_image, "red_image")
    green_img = _as_image(green_image, "green_image")
    blue_img = _as_image(blue_image, "blue_image")
    _remember_base(red_img)
    return sitk.Compose(
        _as_float_image(red_img),
        _as_float_image(green_img),
        _as_float_image(blue_img),
    )


def rgba(
    red_image: object,
    green_image: object,
    blue_image: object,
    alpha_image: object,
) -> sitk.Image:
    """Compose four float images into an RGBA vector image."""
    red_img = _as_image(red_image, "red_image")
    green_img = _as_image(green_image, "green_image")
    blue_img = _as_image(blue_image, "blue_image")
    alpha_img = _as_image(alpha_image, "alpha_image")
    _remember_base(red_img)
    return sitk.Compose(
        _as_float_image(red_img),
        _as_float_image(green_img),
        _as_float_image(blue_img),
        _as_float_image(alpha_img),
    )


def border(img: sitk.Image) -> sitk.Image:
    """True on image border voxels (geometry taken from img)."""
    size = list(img.GetSize())
    ndim = len(size)
    output_pair = _try_native_output(img, sitk.sitkUInt8)
    if output_pair is None:
        shape = tuple(reversed(size))
        result = np.zeros(shape, dtype=np.uint8)
        image = None
    else:
        image, result = output_pair
        result.fill(0)
    for axis in range(ndim):
        low_slice: list[slice | int] = [slice(None)] * ndim
        low_slice[axis] = 0
        result[tuple(low_slice)] = 1

        high_slice: list[slice | int] = [slice(None)] * ndim
        high_slice[axis] = -1
        result[tuple(high_slice)] = 1
    if image is None:
        image = sitk.GetImageFromArray(result, isVector=False)
        image.CopyInformation(img)
    return image


def _coord_image(img: sitk.Image, coord: int) -> sitk.Image:
    size = list(img.GetSize())
    ndim = len(size)
    shape = tuple(reversed(size))
    output_pair = _try_native_output(img, sitk.sitkFloat32)
    if output_pair is None:
        image = None
        result = np.empty(shape, dtype=np.float32)
    else:
        image, result = output_pair
    if coord < ndim:
        axis = ndim - 1 - coord
        broadcast_shape = [1] * ndim
        broadcast_shape[axis] = shape[axis]
        result[...] = np.arange(shape[axis], dtype=np.float32).reshape(broadcast_shape)
    else:
        result.fill(0)
    if image is None:
        image = sitk.GetImageFromArray(result, isVector=False)
        image.CopyInformation(img)
    return image


def x(img: sitk.Image) -> sitk.Image:
    """x-coordinate image (geometry taken from img)."""
    return _coord_image(img, 0)


def y(img: sitk.Image) -> sitk.Image:
    """y-coordinate image (geometry taken from img)."""
    return _coord_image(img, 1)


def z(img: sitk.Image) -> sitk.Image:
    """z-coordinate image (geometry taken from img)."""
    return _coord_image(img, 2)


def _hyperrectangle(size: list[int], hyper_radius: list[int]) -> tuple[np.ndarray, list[list[list[int]]]]:
    ndims = len(size)
    diameter = [(2 * radius) + 1 for radius in hyper_radius]
    small_n_pixels = int(np.prod(diameter, dtype=np.int64))

    displacements = list(size)
    displacements[0] = 1
    for i in range(1, ndims):
        displacements[i] = displacements[i - 1] * size[i - 1]

    dimensional_cursor = [-radius for radius in hyper_radius]
    linear_cursor = 0

    def update_linear_cursor() -> None:
        nonlocal linear_cursor
        linear_cursor = sum(
            dimensional_cursor[i] * displacements[i] for i in range(ndims)
        )

    update_linear_cursor()
    faces: list[list[list[int]]] = [[[[], []][j] for j in range(2)] for _ in range(ndims)]
    indices = np.empty(small_n_pixels, dtype=np.int64)

    def inc() -> None:
        n = 0
        while n < ndims:
            x_val = dimensional_cursor[n] + 1
            y_val = hyper_radius[n]
            if x_val > y_val:
                dimensional_cursor[n] = -y_val
                n += 1
            else:
                dimensional_cursor[n] = x_val
                n = ndims
        update_linear_cursor()

    for i in range(small_n_pixels):
        x_val = linear_cursor
        indices[i] = x_val
        for dim in range(ndims):
            if dimensional_cursor[dim] == -hyper_radius[dim]:
                faces[dim][0].append(x_val)
            elif dimensional_cursor[dim] == hyper_radius[dim]:
                faces[dim][1].append(x_val)
        inc()

    return indices, faces


@lru_cache(maxsize=64)
def _hyperrectangle_cached(
    size_key: tuple[int, ...],
    radius_key: tuple[int, ...],
) -> tuple[np.ndarray, list[list[list[int]]]]:
    return _hyperrectangle([int(v) for v in size_key], [int(v) for v in radius_key])


@lru_cache(maxsize=64)
def _hyperrectangle_numba_faces_cached(
    size_key: tuple[int, ...],
    radius_key: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    indices, faces = _hyperrectangle_cached(size_key, radius_key)
    ndims = len(faces)
    max_len_minus = max((len(faces[dim][0]) for dim in range(ndims)), default=0)
    max_len_plus = max((len(faces[dim][1]) for dim in range(ndims)), default=0)

    faces_minus = np.zeros((ndims, max_len_minus), dtype=np.int64)
    faces_plus = np.zeros((ndims, max_len_plus), dtype=np.int64)
    faces_minus_len = np.zeros(ndims, dtype=np.int64)
    faces_plus_len = np.zeros(ndims, dtype=np.int64)
    for dim in range(ndims):
        minus_face = np.asarray(faces[dim][0], dtype=np.int64)
        plus_face = np.asarray(faces[dim][1], dtype=np.int64)
        faces_minus_len[dim] = minus_face.shape[0]
        faces_plus_len[dim] = plus_face.shape[0]
        if minus_face.shape[0] > 0:
            faces_minus[dim, : minus_face.shape[0]] = minus_face
        if plus_face.shape[0] > 0:
            faces_plus[dim, : plus_face.shape[0]] = plus_face
    return np.asarray(indices, dtype=np.int64), faces_minus, faces_minus_len, faces_plus, faces_plus_len


def _snake(inner_size: list[int], radius: list[int]) -> tuple[np.ndarray, np.ndarray]:
    inner_length = int(np.prod(inner_size, dtype=np.int64))
    outer_size = [n + (2 * radius[i]) for i, n in enumerate(inner_size)]
    ndims = len(radius)

    pathidx = np.zeros(inner_length, dtype=np.int64)
    pathdir = np.zeros(inner_length, dtype=np.int64)

    displacements = list(outer_size)
    displacements[0] = 1
    for i in range(1, ndims):
        displacements[i] = displacements[i - 1] * outer_size[i - 1]

    direction = [1] * ndims
    dimensional_cursor = list(radius)
    linear_cursor = 0

    def update_linear_cursor() -> None:
        nonlocal linear_cursor
        linear_cursor = sum(
            dimensional_cursor[i] * displacements[i] for i in range(ndims)
        )

    def step() -> int:
        res_dir = 0
        n = 0
        while n < ndims:
            d = direction[n]
            x_val = dimensional_cursor[n] + d
            if x_val < radius[n] or x_val >= (radius[n] + inner_size[n]):
                direction[n] = -d
                n += 1
            else:
                res_dir = d * (n + 1)
                dimensional_cursor[n] = x_val
                n = ndims + 1
        update_linear_cursor()
        return res_dir

    update_linear_cursor()
    n = 0
    current_dir = 0
    while n < inner_length:
        pathidx[n] = linear_cursor
        pathdir[n] = current_dir
        n += 1
        current_dir = step()

    return pathidx, pathdir


@lru_cache(maxsize=64)
def _snake_cached(
    inner_size_key: tuple[int, ...],
    radius_key: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray]:
    return _snake([int(v) for v in inner_size_key], [int(v) for v in radius_key])


def _mk_delta(m1: float, m2: float, k: int) -> float:
    return (m2 - m1) / float(k)


def _bin(
    m1: float,
    m2: float,
    delta: float,
    increment: int,
    value: float,
    histogram: np.ndarray,
) -> None:
    if value < m1 or value >= m2:
        return
    if delta == 0.0:
        return
    hist_idx = int((value - m1) / delta)
    histogram[hist_idx] = histogram[hist_idx] + increment


def _hist_corr(h2: np.ndarray, h1: np.ndarray) -> float:
    avg2 = float(np.sum(h2)) / float(h2.size)
    sqrt_den2 = math.sqrt(float(np.sum((h2.astype(np.float64) - avg2) ** 2.0)))

    avg1 = float(np.sum(h1)) / float(h1.size)
    den1 = float(np.sum((h1.astype(np.float64) - avg1) ** 2.0))

    if den1 == 0.0 and sqrt_den2 == 0.0:
        return 1.0
    if den1 == 0.0 or sqrt_den2 == 0.0:
        return 0.0

    num = float(
        np.sum(
            (h1.astype(np.float64) - avg1) * (h2.astype(np.float64) - avg2),
            dtype=np.float64,
        )
    )
    den = math.sqrt(den1) * sqrt_den2
    return num / den


def _box_sum_axis(values: np.ndarray, axis: int, radius: int) -> np.ndarray:
    if radius <= 0:
        return np.asarray(values, dtype=np.int64, order="C")
    pad_width = [(0, 0)] * values.ndim
    pad_width[axis] = (radius, radius)
    padded = np.pad(values, pad_width, mode="constant", constant_values=0)
    csum = np.cumsum(padded, axis=axis, dtype=np.int64)
    zero_shape = list(csum.shape)
    zero_shape[axis] = 1
    csum = np.concatenate([np.zeros(zero_shape, dtype=np.int64), csum], axis=axis)

    lo = [slice(None)] * values.ndim
    hi = [slice(None)] * values.ndim
    window = (2 * radius) + 1
    lo[axis] = slice(0, csum.shape[axis] - window)
    hi[axis] = slice(window, None)
    return csum[tuple(hi)] - csum[tuple(lo)]


def _hist_corr_vectorized(big_histogram: np.ndarray, local_histograms: np.ndarray) -> np.ndarray:
    if local_histograms.size == 0:
        return np.empty(0, dtype=np.float32)

    h2 = np.asarray(big_histogram, dtype=np.float64)
    avg2 = float(np.mean(h2))
    centered2 = h2 - avg2
    den2 = float(np.sum(centered2 * centered2, dtype=np.float64))
    sqrt_den2 = math.sqrt(den2)

    h1 = np.asarray(local_histograms, dtype=np.float64)
    avg1 = np.mean(h1, axis=0)
    centered1 = h1 - avg1
    den1 = np.asarray(np.sum(centered1 * centered1, axis=0, dtype=np.float64), dtype=np.float64)

    result = np.zeros(h1.shape[1], dtype=np.float32)
    both_zero = (den1 == 0.0) & (sqrt_den2 == 0.0)
    result[both_zero] = np.float32(1.0)
    if sqrt_den2 == 0.0:
        return result

    valid = den1 > 0.0
    if np.any(valid):
        num = np.sum(centered1[:, valid] * centered2[:, None], axis=0, dtype=np.float64)
        den = np.sqrt(den1[valid]) * sqrt_den2
        result[valid] = np.asarray(num / den, dtype=np.float32)
    return result


def _crosscorr_kernel_numpy(
    outer_values: np.ndarray,
    outer_shape: tuple[int, ...],
    hidx: np.ndarray,
    ball_radius: list[int],
    big_histogram: np.ndarray,
    m1: float,
    m2: float,
    delta: float,
    nbins: int,
    npixels: int,
    nprocs: int,
) -> np.ndarray:
    temporary_values = np.copy(outer_values)
    if nbins <= 0 or npixels <= 0:
        return temporary_values

    fragsize = npixels // nprocs
    if fragsize <= 0:
        fragsize = npixels
        nprocs = 1

    active = np.zeros(npixels, dtype=bool)
    for procindex in range(nprocs):
        fragstart = procindex * fragsize
        if fragstart >= npixels:
            break
        target = min(fragstart + fragsize - 1, npixels - 1)
        active[fragstart : target + 1] = True
    if not np.any(active):
        return temporary_values

    active_hidx = np.asarray(hidx[active], dtype=np.int64)
    outer_flat = np.asarray(outer_values, dtype=np.float32)

    index_map = np.full(outer_flat.shape[0], -1, dtype=np.int16)
    if delta != 0.0:
        valid = np.logical_and(outer_flat >= m1, outer_flat < m2)
        if np.any(valid):
            raw = np.asarray((outer_flat[valid] - m1) / delta, dtype=np.int64)
            valid_idx = np.logical_and(raw >= 0, raw < nbins)
            valid_positions = np.flatnonzero(valid)
            index_map[valid_positions[valid_idx]] = raw[valid_idx].astype(np.int16, copy=False)

    index_map_nd = index_map.reshape(outer_shape)
    radii = list(reversed(ball_radius))
    bin_axis = np.arange(nbins, dtype=np.int16).reshape((nbins,) + (1,) * len(outer_shape))
    counts = (index_map_nd[None, ...] == bin_axis).astype(np.int64, copy=False)
    for axis, radius in enumerate(radii, start=1):
        counts = _box_sum_axis(counts, axis, radius)
    local_hist = counts.reshape(nbins, -1)[:, active_hidx]

    corr = _hist_corr_vectorized(big_histogram, local_hist)
    temporary_values[active_hidx] = corr
    return temporary_values


@njit(cache=True, inline="always")
def _bin_index_numba(m1: float, m2: float, delta: float, value: float, nbins: int) -> int:
    if value < m1 or value >= m2:
        return -1
    if delta == 0.0:
        return -1
    idx = int((value - m1) / delta)
    if idx < 0 or idx >= nbins:
        return -1
    return idx


@njit(cache=True, nogil=True)
def _prepare_hist_corr_reference_numba(h2: np.ndarray) -> tuple[np.ndarray, float, np.uint8]:
    n = h2.shape[0]
    sum2 = 0.0
    for i in range(n):
        sum2 += float(h2[i])
    avg2 = sum2 / float(n)

    centered2 = np.empty(n, dtype=np.float64)
    den2 = 0.0
    for i in range(n):
        d2 = float(h2[i]) - avg2
        centered2[i] = d2
        den2 += d2 * d2
    return centered2, math.sqrt(den2), np.uint8(1 if den2 == 0.0 else 0)


@njit(cache=True, inline="always")
def _hist_corr_numba(
    h2_centered: np.ndarray,
    h2_sqrt_den: float,
    h2_is_constant: np.uint8,
    h1: np.ndarray,
) -> float:
    n = h2_centered.shape[0]
    sum1 = 0.0
    for i in range(n):
        sum1 += float(h1[i])
    avg1 = sum1 / float(n)

    den1 = 0.0
    num = 0.0
    for i in range(n):
        d1 = float(h1[i]) - avg1
        den1 += d1 * d1
        num += d1 * h2_centered[i]

    if den1 == 0.0 and h2_is_constant == 1:
        return 1.0
    if den1 == 0.0 or h2_is_constant == 1:
        return 0.0
    return num / (math.sqrt(den1) * h2_sqrt_den)


@njit(cache=True, nogil=True)
def _build_big_histogram_numba(
    values: np.ndarray,
    mask_values: np.ndarray,
    m1: float,
    m2: float,
    delta: float,
    nbins: int,
) -> np.ndarray:
    hist = np.zeros(nbins, dtype=np.int64)
    for i in range(values.shape[0]):
        if mask_values[i] > 0:
            hist_idx = _bin_index_numba(m1, m2, delta, values[i], nbins)
            if hist_idx >= 0:
                hist[hist_idx] += 1
    return hist


@njit(cache=True, parallel=True, nogil=True)
def _crosscorr_kernel_numba(
    outer_values: np.ndarray,
    hidx: np.ndarray,
    hdir: np.ndarray,
    indices: np.ndarray,
    faces_minus: np.ndarray,
    faces_minus_len: np.ndarray,
    faces_plus: np.ndarray,
    faces_plus_len: np.ndarray,
    ref_centered_hist: np.ndarray,
    ref_sqrt_den: float,
    ref_is_constant: np.uint8,
    m1: float,
    m2: float,
    delta: float,
    nbins: int,
    npixels: int,
    nprocs: int,
) -> np.ndarray:
    temporary_values = np.copy(outer_values)
    fragsize = npixels // nprocs
    if fragsize <= 0:
        fragsize = npixels
        nprocs = 1

    for procindex in prange(nprocs):
        fragstart = procindex * fragsize
        if fragstart >= npixels:
            continue

        start = int(hidx[fragstart])
        local_hist = np.zeros(nbins, dtype=np.int64)
        for i in range(indices.shape[0]):
            linear_coord = start + int(indices[i])
            hist_idx = _bin_index_numba(m1, m2, delta, outer_values[linear_coord], nbins)
            if hist_idx >= 0:
                local_hist[hist_idx] += 1

        temporary_values[start] = np.float32(
            _hist_corr_numba(ref_centered_hist, ref_sqrt_den, ref_is_constant, local_hist)
        )

        target = fragstart + fragsize - 1
        previous = start
        upper = target
        if upper > npixels - 1:
            upper = npixels - 1
        for pos in range(fragstart + 1, upper + 1):
            center = int(hidx[pos])
            direction = int(hdir[pos])
            face_idx = abs(direction) - 1
            remove_face = faces_minus
            remove_len = faces_minus_len
            add_face = faces_plus
            add_len = faces_plus_len
            if direction < 0:
                remove_face = faces_plus
                remove_len = faces_plus_len
                add_face = faces_minus
                add_len = faces_minus_len

            for j in range(remove_len[face_idx]):
                linear_el = int(remove_face[face_idx, j])
                linear_coord = previous + linear_el
                hist_idx = _bin_index_numba(m1, m2, delta, outer_values[linear_coord], nbins)
                if hist_idx >= 0:
                    local_hist[hist_idx] -= 1

            for j in range(add_len[face_idx]):
                linear_el = int(add_face[face_idx, j])
                linear_coord = center + linear_el
                hist_idx = _bin_index_numba(m1, m2, delta, outer_values[linear_coord], nbins)
                if hist_idx >= 0:
                    local_hist[hist_idx] += 1

            temporary_values[center] = np.float32(
                _hist_corr_numba(ref_centered_hist, ref_sqrt_den, ref_is_constant, local_hist)
            )
            previous = center

    return temporary_values


def crossCorrelation(
    rad: float,
    a: object,
    b: object,
    fb: object,
    m1: float,
    m2: float,
    k: float,
) -> sitk.Image:
    """Local cross-correlation map between two images over a spherical neighbourhood."""
    a_image = _as_float_image(_as_image(a, "a"))
    b_image = _as_float_image(_as_image(b, "b"))
    fb_image = _as_bool_image(_as_image(fb, "fb"))
    _remember_base(a_image)

    npixels = int(a_image.GetNumberOfPixels())
    spacing = a_image.GetSpacing()
    ball_radius: list[int] = []
    for i in range(len(spacing)):
        vox_radius = int(round(float(rad) / float(spacing[i])))
        if vox_radius == 0:
            vox_radius = 1
        ball_radius.append(vox_radius)

    outer_image = sitk.ConstantPad(a_image, ball_radius, ball_radius, float("inf"))
    size = [int(x) for x in a_image.GetSize()]

    nbins = int(k)
    delta = _mk_delta(float(m1), float(m2), nbins)

    b_values = _flatten_image(b_image, np.float32)
    fb_values = _flatten_image(fb_image, np.uint8)
    if _HAS_NUMBA:
        big_histogram = _build_big_histogram_numba(
            np.asarray(b_values, dtype=np.float32),
            np.asarray(fb_values, dtype=np.uint8),
            float(m1),
            float(m2),
            float(delta),
            int(nbins),
        )
    else:
        big_histogram = np.zeros(nbins, dtype=np.int64)
        for linear_coord in range(b_values.size):
            if fb_values[linear_coord] > 0:
                _bin(
                    float(m1),
                    float(m2),
                    delta,
                    1,
                    float(b_values[linear_coord]),
                    big_histogram,
                )

    outer_array = sitk.GetArrayViewFromImage(outer_image)
    outer_values = outer_array.reshape(-1).astype(np.float32, copy=False)
    size_key = tuple(int(v) for v in size)
    radius_key = tuple(int(v) for v in ball_radius)
    hidx, hdir = _snake_cached(size_key, radius_key)
    backend = _crosscorr_backend()
    if backend == "numba" and _HAS_NUMBA:
        nprocs = max(1, int(get_num_threads()))
    else:
        nprocs = os.cpu_count() or 1
    needs_faces = backend == "numba" or backend == "python"
    indices: np.ndarray | None = None
    faces: list[list[list[int]]] | None = None
    numba_faces: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
    if needs_faces:
        outer_size = [int(x) for x in outer_image.GetSize()]
        outer_size_key = tuple(int(v) for v in outer_size)
        indices, faces = _hyperrectangle_cached(outer_size_key, radius_key)
        if backend == "numba" and _HAS_NUMBA:
            numba_faces = _hyperrectangle_numba_faces_cached(outer_size_key, radius_key)

    if backend == "numba" and _HAS_NUMBA:
        assert numba_faces is not None
        numba_indices, faces_minus, faces_minus_len, faces_plus, faces_plus_len = numba_faces
        ref_centered_hist, ref_sqrt_den, ref_is_constant = _prepare_hist_corr_reference_numba(
            np.asarray(big_histogram, dtype=np.int64)
        )

        temporary_values = _crosscorr_kernel_numba(
            np.asarray(outer_values, dtype=np.float32),
            np.asarray(hidx, dtype=np.int64),
            np.asarray(hdir, dtype=np.int64),
            numba_indices,
            faces_minus,
            faces_minus_len,
            faces_plus,
            faces_plus_len,
            ref_centered_hist,
            float(ref_sqrt_den),
            np.uint8(ref_is_constant),
            float(m1),
            float(m2),
            float(delta),
            int(nbins),
            int(npixels),
            int(nprocs),
        )
    elif backend == "numpy":
        temporary_values = _crosscorr_kernel_numpy(
            np.asarray(outer_values, dtype=np.float32),
            tuple(int(v) for v in outer_array.shape),
            np.asarray(hidx, dtype=np.int64),
            ball_radius,
            np.asarray(big_histogram, dtype=np.int64),
            float(m1),
            float(m2),
            float(delta),
            int(nbins),
            int(npixels),
            int(nprocs),
        )
    else:
        assert faces is not None
        assert indices is not None
        temporary_values = np.array(outer_values, copy=True)

        def local_add(
            local_histogram: np.ndarray,
            linear_center: int,
            increment: int,
            linear_el: int,
        ) -> None:
            linear_coord = linear_center + linear_el
            _bin(
                float(m1),
                float(m2),
                delta,
                increment,
                float(outer_values[linear_coord]),
                local_histogram,
            )

        fragsize = npixels // nprocs
        for procindex in range(nprocs):
            fragstart = procindex * fragsize
            if fragstart >= npixels:
                break

            start = int(hidx[fragstart])
            local_hist = np.zeros(nbins, dtype=np.int64)
            for linear_el in indices:
                local_add(local_hist, start, 1, int(linear_el))
            temporary_values[start] = np.float32(_hist_corr(big_histogram, local_hist))

            target = fragstart + fragsize - 1
            previous = start
            for pos in range(fragstart + 1, min(target, npixels - 1) + 1):
                center = int(hidx[pos])
                direction = int(hdir[pos])
                face_idx = abs(direction) - 1
                face_minus = faces[face_idx][0]
                face_plus = faces[face_idx][1]
                if direction < 0:
                    face_minus, face_plus = face_plus, face_minus

                for linear_el in face_minus:
                    local_add(local_hist, previous, -1, int(linear_el))
                for linear_el in face_plus:
                    local_add(local_hist, center, 1, int(linear_el))

                temporary_values[center] = np.float32(_hist_corr(big_histogram, local_hist))
                previous = center

    temporary_image = _make_image_from_flat(
        temporary_values,
        outer_array.shape,
        outer_image,
        np.float32,
    )
    return sitk.Crop(temporary_image, ball_radius, ball_radius)
