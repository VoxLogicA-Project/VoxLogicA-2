"""``near`` -- bit-identical to sitk.BinaryDilate on the native fast path.

The native path replaces a 27-voxel box dilation with three separable 1D max
passes. Two things about that rewrite are easy to get wrong and were both
wrong in the shipped kernel until these tests existed:

1. The passes ping-pong between two buffers, so which argument holds the
   final result is a property of the pass COUNT, not of the parameter names.
   An earlier revision landed the third pass in the buffer the caller was
   treating as scratch, and the caller copied the other one back -- shipping a
   two-axis dilation. Every test here therefore goes through ``near()``, the
   real entry point, never the kernel directly: a kernel-level test would have
   passed while production stayed broken.

2. ``sitk.BinaryDilate(..., foregroundValue=1.0)`` is not "max over the box".
   It copies the input and then sets dilated pixels to 1, so a voxel holding
   some other non-zero value is neither foreground nor erased. Such values
   reach the kernel whenever the input was not already 0/1, because
   ``_as_bool_image`` casts (truncating) rather than thresholds -- hence the
   non-binary and float cases below, which a 0/1-only suite would miss.

Volumes only, and deliberately including non-cubic, unit-extent and
single-voxel shapes: an axis of length 1 is where the clamped-index boundary
handling degenerates.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.vox1 import kernels


def _itk_near(volume: np.ndarray) -> np.ndarray:
    image = sitk.GetImageFromArray(volume)
    return sitk.GetArrayFromImage(
        sitk.BinaryDilate(kernels._as_bool_image(image), [1, 1, 1], sitk.sitkBox, 1.0)
    )


def _near(volume: np.ndarray) -> np.ndarray:
    return sitk.GetArrayFromImage(kernels.near(sitk.GetImageFromArray(volume)))


SHAPES = [(6, 7, 8), (5, 5, 5), (3, 9, 4), (1, 8, 8), (8, 1, 8), (8, 8, 1),
          (2, 2, 2), (1, 1, 1), (31, 4, 29), (20, 17, 13)]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("density", [0.0, 0.05, 0.2, 0.6, 1.0])
def test_binary_matches_itk(shape, density):
    rng = np.random.default_rng(abs(hash((shape, density))) % (2**32))
    volume = (rng.random(shape) < density).astype(np.uint8)
    np.testing.assert_array_equal(_near(volume), _itk_near(volume))


def test_dilates_along_every_axis():
    """A single interior voxel must light up all 26 neighbours.

    This is the direct regression test for the dropped x pass: with any one
    of the three passes missing the result is a plane or a line, not a cube,
    and the count below is 9 or 3 instead of 27.
    """
    volume = np.zeros((5, 5, 5), np.uint8)
    volume[2, 2, 2] = 1
    got = _near(volume)
    assert int(got.sum()) == 27
    np.testing.assert_array_equal(got[1:4, 1:4, 1:4], np.ones((3, 3, 3), np.uint8))


@pytest.mark.parametrize("corner", [(0, 0, 0), (0, 4, 4), (4, 0, 4), (4, 4, 0), (4, 4, 4)])
def test_corner_voxel_matches_itk(corner):
    """Boundary clamping must not wrap, duplicate or drop the edge."""
    volume = np.zeros((5, 5, 5), np.uint8)
    volume[corner] = 1
    np.testing.assert_array_equal(_near(volume), _itk_near(volume))
    assert int(_near(volume).sum()) == 8


@pytest.mark.parametrize("shape", [(4, 5, 6), (9, 3, 7), (12, 12, 12)])
def test_non_binary_uint8_matches_itk(shape):
    """Values other than 0/1 are neither foreground nor erased."""
    rng = np.random.default_rng(abs(hash(shape)) % (2**32))
    volume = rng.integers(0, 7, shape).astype(np.uint8)
    np.testing.assert_array_equal(_near(volume), _itk_near(volume))


@pytest.mark.parametrize("shape", [(4, 5, 6), (12, 12, 12)])
def test_full_uint8_range_matches_itk(shape):
    rng = np.random.default_rng(abs(hash(shape)) % (2**32))
    volume = rng.integers(0, 256, shape).astype(np.uint8)
    np.testing.assert_array_equal(_near(volume), _itk_near(volume))


@pytest.mark.parametrize("scale", [3.0, 300.0])
def test_float_input_matches_itk(scale):
    """Float input is cast (truncating) before dilation; 256.0 becomes 0."""
    rng = np.random.default_rng(7)
    volume = (rng.random((6, 5, 7)) * scale).astype(np.float32)
    np.testing.assert_array_equal(_near(volume), _itk_near(volume))


def test_idempotent_on_saturated_volume():
    volume = np.ones((4, 4, 4), np.uint8)
    np.testing.assert_array_equal(_near(volume), volume)
