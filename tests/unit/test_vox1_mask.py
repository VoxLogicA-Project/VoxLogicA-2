"""``mask`` — bit-identical to sitk.Mask on the native fast path.

The native path is a jitted select writing straight into the pooled output.
The obvious vectorized alternative (``img * (mask != 0)``) is wrong on
non-finite voxels, so these tests pin the NaN/inf behavior explicitly rather
than only checking ordinary values.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.vox1 import kernels


def _itk_mask(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return sitk.GetArrayFromImage(
        sitk.Mask(sitk.GetImageFromArray(image),
                  sitk.Cast(sitk.GetImageFromArray(mask), sitk.sitkUInt8), 0.0)
    )


def _mask(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    # GetArrayFromImage, not GetArrayViewFromImage: a view aliases the sitk
    # image's buffer, and the result here is a temporary whose buffer would be
    # freed before the view is read.
    return sitk.GetArrayFromImage(
        kernels.mask(sitk.GetImageFromArray(image), sitk.GetImageFromArray(mask))
    )


@pytest.mark.parametrize("dtype", [np.float32, np.uint8])
def test_matches_itk_mask(dtype):
    shape = (7, 11, 13)
    rng = np.random.default_rng(0)
    image = (rng.random(shape) * 250).astype(dtype)
    mask = (rng.random(shape) > 0.5).astype(np.uint8)
    np.testing.assert_array_equal(_mask(image, mask), _itk_mask(image, mask))


def test_mask_is_nonzero_not_equal_to_one():
    """A mask of 7s selects exactly as a mask of 1s — the convention is
    ``!= 0``, which is what sitk.Mask applies after its UInt8 coercion."""
    shape = (4, 5, 6)
    image = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    mask = np.zeros(shape, dtype=np.uint8)
    mask[..., ::2] = 7
    np.testing.assert_array_equal(_mask(image, mask), _itk_mask(image, mask))


def test_masked_out_non_finite_voxels_become_zero():
    """NaN and +/-inf under a false mask must read back as 0.

    ``img * (mask != 0)`` — the natural vectorized rewrite of this kernel —
    silently fails here, because NaN*0 is NaN.
    """
    shape = (3, 4, 5)
    image = np.full(shape, 1.0, dtype=np.float32)
    image[0, 0, 0] = np.nan
    image[0, 0, 1] = np.inf
    image[0, 0, 2] = -np.inf
    mask = np.ones(shape, dtype=np.uint8)
    mask[0, 0, 0:3] = 0

    result = _mask(image, mask)
    assert result[0, 0, 0] == 0.0
    assert result[0, 0, 1] == 0.0
    assert result[0, 0, 2] == 0.0
    np.testing.assert_array_equal(result, _itk_mask(image, mask))


def test_non_finite_voxels_survive_a_true_mask():
    shape = (3, 4, 5)
    image = np.full(shape, 1.0, dtype=np.float32)
    image[1, 1, 1] = np.nan
    image[1, 1, 2] = np.inf
    mask = np.ones(shape, dtype=np.uint8)

    result = _mask(image, mask)
    assert np.isnan(result[1, 1, 1])
    assert np.isposinf(result[1, 1, 2])


@pytest.mark.parametrize("fill", [0, 1])
def test_constant_masks(fill):
    shape = (3, 4, 5)
    rng = np.random.default_rng(1)
    image = (rng.random(shape) * 100).astype(np.float32)
    mask = np.full(shape, fill, dtype=np.uint8)
    np.testing.assert_array_equal(_mask(image, mask), _itk_mask(image, mask))
