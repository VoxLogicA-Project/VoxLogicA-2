"""vox1's numpy-native kernels (not/and/or/eq_sv/geq_sv/leq_sv/between/mask
and the six generic comparisons) -- bit-identical against the real sitk
filter each one replaces, including NaN and negative/boundary values.

These are KERNEL-level tests (call the Python function directly), not
through the engine -- deliberately, so a wiring bug in the executor's
protocol dispatch (numpy vs sitk unwrap) can't mask a kernel bug or vice
versa. See test_numpy_native_engine_integration.py for the through-the-engine
check (dtype/geometry threading, mixed numpy-native/sitk chains).

One value in the fixture array is NaN and one is exactly 0.0/at the 1.0
threshold -- three of the four cases that could plausibly diverge between an
ITK filter and a hand-translated numpy expression (NaN propagation, exact
threshold inclusion, sign). All three were checked against REAL sitk output
before being trusted, not derived by hand: the first attempt at this had a
comparison bug of its own (2D vs flattened shape) that looked like 16
kernel bugs until the harness, not the kernels, was fixed -- worth remembering
before trusting any "it doesn't match" result at face value either.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.vox1 import kernels

# 0.1, 0.5 (leq_sv threshold), 1.0 (eq_sv/between/comparison threshold), 1.5,
# 2.0, -1.0 (negative), NaN, 0.0 (exact zero), 3.0, -3.0.
_ARR = np.array([0.1, 0.5, 1.0, 1.5, 2.0, -1.0, np.nan, 0.0, 3.0, -3.0], dtype=np.float32)


def _itk(image_array: np.ndarray) -> sitk.Image:
    """sitk rejects a bare 1D array ("unsupported number of dimensions");
    give it a 2D shape without changing the flattened content any of the
    tests actually compare (all comparisons here flatten before checking)."""
    flat = np.asarray(image_array).reshape(-1)
    return sitk.GetImageFromArray(flat.reshape(1, flat.shape[0]))


def _flat_equal(itk_result, np_result) -> bool:
    a = sitk.GetArrayFromImage(itk_result) if isinstance(itk_result, sitk.Image) else itk_result
    return bool(np.array_equal(np.asarray(a).flatten(), np.asarray(np_result).flatten()))


@pytest.mark.unit
def test_not_matches_sitk_not_including_nan():
    src = (_ARR > 0).astype(np.uint8)
    itk_out = sitk.Not(sitk.Cast(_itk(src) > 0, sitk.sitkUInt8))
    assert _flat_equal(itk_out, kernels.logical_not(src))


@pytest.mark.unit
def test_not_normalizes_via_nonzero_not_raw_bitwise_complement():
    """The specific mistake this module's header warns about: sitk.Not is
    NOT ~x (which would give 254 for input 1) -- confirmed against real sitk
    output, not assumed from and/or's (genuinely different) raw-bitwise
    behavior."""
    raw = np.array([0, 1, 2, 5, 255], dtype=np.uint8)
    itk_out = sitk.GetArrayFromImage(sitk.Not(_itk(raw)))
    assert np.array_equal(itk_out.flatten(), kernels.logical_not(raw))
    assert not np.array_equal(itk_out.flatten(), (~raw))  # the wrong translation


@pytest.mark.unit
def test_and_or_are_raw_bitwise_not_normalized():
    """The other half of that same distinction: and/or do NOT normalize via
    !=0 first -- confirmed against sitk.And/.Or on non-0/1 pixel values."""
    a = np.array([5, 3, 1, 0, 7], dtype=np.uint8)
    b = np.array([1, 1, 0, 0, 2], dtype=np.uint8)
    itk_and = sitk.GetArrayFromImage(sitk.And(_itk(a), _itk(b)))
    itk_or = sitk.GetArrayFromImage(sitk.Or(_itk(a), _itk(b)))
    assert np.array_equal(itk_and.flatten(), kernels.logical_and(a, b))
    assert np.array_equal(itk_or.flatten(), kernels.logical_or(a, b))


@pytest.mark.unit
@pytest.mark.parametrize("scalar", [True, False, 1, 0])
def test_and_or_scalar_operand_broadcasts_as_sitk_does(scalar):
    """sitk.And(image, True/False/1/0) broadcasts a 0/1 uint8 value, NOT the
    raw int (confirmed empirically) -- e.g. sitk.And(img, 5) would NOT mean
    'bitwise-and with 5' the way and(img, some_other_image) does."""
    arr = np.array([5, 3, 1, 0, 7], dtype=np.uint8)
    itk_and = sitk.GetArrayFromImage(sitk.And(_itk(arr), scalar))
    itk_or = sitk.GetArrayFromImage(sitk.Or(_itk(arr), scalar))
    assert np.array_equal(itk_and.flatten(), kernels.logical_and(arr, scalar))
    assert np.array_equal(itk_or.flatten(), kernels.logical_or(arr, scalar))


@pytest.mark.unit
def test_and_or_both_scalar_matches_python_bool_semantics():
    """No image at all: matches the pre-existing scalar-only branch exactly
    (a Python bool, not an array)."""
    assert kernels.logical_and(True, False) is False
    assert kernels.logical_and(True, True) is True
    assert kernels.logical_or(False, False) is False
    assert kernels.logical_or(False, True) is True


@pytest.mark.unit
def test_eq_geq_leq_sv_match_sitk_including_nan_and_boundary():
    img = _itk(_ARR)
    assert _flat_equal(sitk.Cast(img == 1.0, sitk.sitkUInt8), kernels.eq_sv(1.0, _ARR))
    assert _flat_equal(sitk.GreaterEqual(img, 0.5), kernels.geq_sv(0.5, _ARR))
    assert _flat_equal(sitk.LessEqual(img, 0.5), kernels.leq_sv(0.5, _ARR))


@pytest.mark.unit
def test_between_is_inclusive_on_both_ends_matching_binary_threshold():
    img = _itk(_ARR)
    itk_out = sitk.BinaryThreshold(img, 0.0, 1.5, 1, 0)
    assert _flat_equal(itk_out, kernels.between(0.0, 1.5, _ARR))


@pytest.mark.unit
def test_mask_matches_sitk_mask_and_preserves_image_dtype():
    cond = (_ARR > 0.5).astype(np.uint8)
    img = _itk(_ARR)
    itk_out = sitk.Mask(img, sitk.Cast(_itk(cond), sitk.sitkUInt8), 0.0)
    assert _flat_equal(itk_out, kernels.mask(_ARR, cond))

    uint8_img = np.array([10, 20, 30, 0], dtype=np.uint8)
    uint8_cond = np.array([1, 0, 1, 0], dtype=np.uint8)
    result = kernels.mask(uint8_img, uint8_cond)
    assert result.dtype == np.uint8, "mask's output dtype must track its image argument"
    assert np.array_equal(result, [10, 0, 30, 0])


@pytest.mark.unit
@pytest.mark.parametrize("op_name,itk_op", [
    ("equal", sitk.Equal), ("not_equal", sitk.NotEqual),
    ("less", sitk.Less), ("less_equal", sitk.LessEqual),
    ("greater", sitk.Greater), ("greater_equal", sitk.GreaterEqual),
])
def test_generic_comparison_image_vs_image_and_image_vs_scalar(op_name, itk_op):
    kernel = getattr(kernels, op_name)
    other = _ARR[::-1].copy()
    img, img2 = _itk(_ARR), _itk(other)

    assert _flat_equal(itk_op(img, img2), kernel(_ARR, other)), f"{op_name} image-vs-image"
    assert _flat_equal(itk_op(img, 1.0), kernel(_ARR, 1.0)), f"{op_name} image-vs-scalar"


@pytest.mark.unit
@pytest.mark.parametrize("op_name,flipped_itk_op", [
    # kernels.py's comparison flip table, for the case the SCALAR is on the
    # left: e.g. "1.0 < img" must mean the same as "img > 1.0", and the ITK
    # reference is built with the FLIPPED filter + flipped operand order to
    # avoid circularly using the same flip logic under test.
    ("equal", sitk.Equal), ("not_equal", sitk.NotEqual),
    ("less", sitk.Greater), ("less_equal", sitk.GreaterEqual),
    ("greater", sitk.Less), ("greater_equal", sitk.LessEqual),
])
def test_generic_comparison_scalar_on_the_left(op_name, flipped_itk_op):
    kernel = getattr(kernels, op_name)
    img = _itk(_ARR)
    expected = flipped_itk_op(img, 1.0)  # e.g. less: "1.0 < img" == "img > 1.0"
    assert _flat_equal(expected, kernel(1.0, _ARR))


@pytest.mark.unit
def test_generic_comparison_both_scalar_returns_python_bool():
    assert kernels.less(1.0, 2.0) is True
    assert kernels.less(2.0, 1.0) is False
    assert kernels.equal(1.0, 1.0) is True
