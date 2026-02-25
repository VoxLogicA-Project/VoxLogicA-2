"""VoxLogicA experimental-branch compatibility kernels."""

from __future__ import annotations

from threading import RLock
import os
import math

import numpy as np
import SimpleITK as sitk

from voxlogica.primitives.default._sequence_math import apply_binary_op

_BASE_IMAGE: sitk.Image | None = None
_BASE_IMAGE_LOCK = RLock()


def reset_runtime_state() -> None:
    """Reset namespace runtime state before a new execution run."""
    global _BASE_IMAGE
    with _BASE_IMAGE_LOCK:
        _BASE_IMAGE = None


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
    return sitk.Cast(image, sitk.sitkUInt8)


def _as_float_image(image: sitk.Image) -> sitk.Image:
    return sitk.Cast(image, sitk.sitkFloat32)


def _flatten_image(image: sitk.Image, dtype: np.dtype | None = None) -> np.ndarray:
    data = sitk.GetArrayFromImage(image).reshape(-1)
    if dtype is None:
        return np.asarray(data)
    return np.asarray(data, dtype=dtype)


def _make_image_from_flat(
    flat: np.ndarray,
    shape: tuple[int, ...],
    reference: sitk.Image,
    dtype: np.dtype,
) -> sitk.Image:
    array = np.asarray(flat, dtype=dtype).reshape(shape)
    image = sitk.GetImageFromArray(array, isVector=False)
    image.CopyInformation(reference)
    return image


def num_div(left: float, right: float) -> float:
    return float(left) / float(right)


def num_mul(left: float, right: float) -> float:
    return float(left) * float(right)


def num_add(left: float, right: float) -> float:
    return float(left) + float(right)


def num_sub(left: float, right: float) -> float:
    return float(left) - float(right)


def bool_and_scalar(left: bool, right: bool) -> bool:
    return bool(left) and bool(right)


def bool_or_scalar(left: bool, right: bool) -> bool:
    return bool(left) or bool(right)


def bool_not_scalar(value: bool) -> bool:
    return not bool(value)


def num_eq(left: float, right: float) -> bool:
    return float(left) == float(right)


def num_leq(left: float, right: float) -> bool:
    return float(left) <= float(right)


def num_lt(left: float, right: float) -> bool:
    return float(left) < float(right)


def num_geq(left: float, right: float) -> bool:
    return float(left) >= float(right)


def num_gt(left: float, right: float) -> bool:
    return float(left) > float(right)


def bconstant(value: bool) -> sitk.Image:
    if bool(value):
        return tt()
    return ff()


def tt() -> sitk.Image:
    base = _require_base()
    return _filled_image_like(base, sitk.sitkUInt8, 1)


def ff() -> sitk.Image:
    base = _require_base()
    return _filled_image_like(base, sitk.sitkUInt8, 0)


def logical_not(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Not(img)


def logical_and(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.And(left, right)
    return bool(left) and bool(right)


def logical_or(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Or(left, right)
    return bool(left) or bool(right)


def dt(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    flt = sitk.SignedMaurerDistanceMapImageFilter()
    flt.SetInsideIsPositive(False)
    flt.SetSquaredDistance(False)
    flt.SetUseImageSpacing(True)
    flt.SetBackgroundValue(0.0)
    return flt.Execute(_as_bool_image(img))


def constant(value: float) -> sitk.Image:
    base = _require_base()
    return _filled_image_like(base, sitk.sitkFloat32, float(value))


def eq_sv(value: float, image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.BinaryThreshold(img, float(value), float(value), 1, 0)


def geq_sv(value: float, image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.GreaterEqual(img, float(value))


def leq_sv(value: float, image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.LessEqual(img, float(value))


def between(value1: float, value2: float, image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.BinaryThreshold(img, float(value1), float(value2), 1, 0)


def max_value(image: object) -> float:
    img = _as_image(image, "image")
    _remember_base(img)
    flt = sitk.MinimumMaximumImageFilter()
    flt.Execute(img)
    return float(flt.GetMaximum())


def abs_value(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Abs(img)


def min_value(image: object) -> float:
    img = _as_image(image, "image")
    _remember_base(img)
    flt = sitk.MinimumMaximumImageFilter()
    flt.Execute(img)
    return float(flt.GetMinimum())


def _add_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Add(left, right)
    return float(left) + float(right)


def _mul_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Multiply(left, right)
    return float(left) * float(right)


def _div_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Divide(left, right)
    return float(left) / float(right)


def _sub_values(left: object, right: object) -> object:
    if _is_image(left) or _is_image(right):
        _remember_base_from_values(left, right)
        return sitk.Subtract(left, right)
    return float(left) - float(right)


def add(left: object, right: object) -> object:
    return apply_binary_op("Add", left, right, _add_values)


def multiply(left: object, right: object) -> object:
    return apply_binary_op("Multiply", left, right, _mul_values)


def divide(left: object, right: object) -> object:
    return apply_binary_op("Division", left, right, _div_values)


def subtract(left: object, right: object) -> object:
    return apply_binary_op("Subtraction", left, right, _sub_values)


def mask(image: object, mask_image: object) -> sitk.Image:
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)
    return sitk.Mask(img, _as_bool_image(msk), 0.0)


def avg(image: object, mask_image: object) -> float:
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


def div_sv(value: float, image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Divide(float(value), img)


def sub_sv(value: float, image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Subtract(float(value), img)


def div_vs(image: object, value: float) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Multiply(img, 1.0 / float(value))


def sub_vs(image: object, value: float) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Subtract(img, float(value))


def add_vs(image: object, value: float) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Add(img, float(value))


def mul_vs(image: object, value: float) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.Multiply(img, float(value))


def near(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.BinaryDilate(_as_bool_image(img), [1, 1, 1], sitk.sitkBox, 1.0)


def interior(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    return sitk.BinaryErode(_as_bool_image(img), [1, 1, 1], sitk.sitkBox, 0.0)


def _label_connected_components(image: sitk.Image) -> tuple[sitk.Image, int]:
    labels = sitk.ConnectedComponent(_as_bool_image(image), True)
    labels_array = _flatten_image(labels, np.uint32)
    max_label = int(labels_array.max(initial=0))
    return labels, max_label


def lcc(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
    labels, _ = _label_connected_components(img)
    return sitk.Cast(labels, sitk.sitkFloat32)


def Lcc(image: object) -> sitk.Image:
    return lcc(image)


def through(image1: object, image2: object) -> sitk.Image:
    img1 = _as_image(image1, "image1")
    img2 = _as_image(image2, "image2")
    _remember_base(img1)

    cc_image, _ = _label_connected_components(img2)
    masked = sitk.Mask(cc_image, _as_bool_image(img1), 0.0)

    cc_values = _flatten_image(cc_image, np.uint32)
    masked_values = _flatten_image(masked, np.uint32)
    max_label = int(cc_values.max(initial=0))

    flags = np.zeros(max_label + 1, dtype=np.uint8)
    active = masked_values[masked_values > 0]
    if active.size > 0:
        flags[active] = 1

    result_values = np.zeros_like(cc_values, dtype=np.uint8)
    non_background = cc_values > 0
    result_values[non_background] = flags[cc_values[non_background]]

    shape = sitk.GetArrayViewFromImage(cc_image).shape
    return _make_image_from_flat(result_values, shape, cc_image, np.uint8)


def volume(image: object) -> float:
    img = _as_image(image, "image")
    _remember_base(img)
    values = _flatten_image(_as_bool_image(img), np.uint8)
    return float(np.count_nonzero(values > 0))


def vol(image: object) -> float:
    return volume(image)


def otsu(image: object, mask_image: object, nbins: float) -> sitk.Image:
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


def maxvol(image: object) -> sitk.Image:
    img = _as_image(image, "image")
    _remember_base(img)
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


def percentiles(image: object, mask_image: object, correction: float) -> sitk.Image:
    img = _as_image(image, "image")
    msk = _as_image(mask_image, "mask_image")
    _remember_base(img)

    img_values = _flatten_image(_as_float_image(img), np.float32)
    mask_values = _flatten_image(_as_bool_image(msk), np.uint8)
    if img_values.shape[0] != mask_values.shape[0]:
        raise ValueError("percentiles requires images with the same number of voxels")

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
    return sitk.Cast(sitk.VectorIndexSelectionCast(img, int(index)), sitk.sitkFloat32)


def red(model: object) -> sitk.Image:
    return _component(model, 0)


def green(model: object) -> sitk.Image:
    return _component(model, 1)


def blue(model: object) -> sitk.Image:
    return _component(model, 2)


def alpha(model: object) -> sitk.Image:
    img = _as_image(model, "model")
    _remember_base(img)
    if img.GetNumberOfComponentsPerPixel() < 4:
        return _filled_image_like(img, sitk.sitkFloat32, 255.0)
    return _component(img, 3)


def rgb(red_image: object, green_image: object, blue_image: object) -> sitk.Image:
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


def border() -> sitk.Image:
    base = _require_base()
    size = list(base.GetSize())
    ndim = len(size)
    shape = tuple(reversed(size))
    result = np.zeros(shape, dtype=np.uint8)
    for axis in range(ndim):
        low_slice = [slice(None)] * ndim
        low_slice[axis] = 0
        result[tuple(low_slice)] = 1

        high_slice = [slice(None)] * ndim
        high_slice[axis] = -1
        result[tuple(high_slice)] = 1

    image = sitk.GetImageFromArray(result, isVector=False)
    image.CopyInformation(base)
    return image


def _coord_image(coord: int) -> sitk.Image:
    base = _require_base()
    size = list(base.GetSize())
    ndim = len(size)
    shape = tuple(reversed(size))
    result = np.zeros(shape, dtype=np.float32)
    if coord < ndim:
        axis = ndim - 1 - coord
        result = np.indices(shape, dtype=np.float32)[axis]
    image = sitk.GetImageFromArray(result, isVector=False)
    image.CopyInformation(base)
    return image


def x() -> sitk.Image:
    return _coord_image(0)


def y() -> sitk.Image:
    return _coord_image(1)


def z() -> sitk.Image:
    return _coord_image(2)


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


def crossCorrelation(
    rad: float,
    a: object,
    b: object,
    fb: object,
    m1: float,
    m2: float,
    k: float,
) -> sitk.Image:
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
    outer_size = [int(x) for x in outer_image.GetSize()]
    indices, faces = _hyperrectangle(outer_size, ball_radius)

    nbins = int(k)
    delta = _mk_delta(float(m1), float(m2), nbins)

    big_histogram = np.zeros(nbins, dtype=np.int64)
    b_values = _flatten_image(b_image, np.float32)
    fb_values = _flatten_image(fb_image, np.uint8)
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
    temporary_values = np.array(outer_values, copy=True)

    hidx, hdir = _snake(size, ball_radius)

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

    nprocs = os.cpu_count() or 1
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
