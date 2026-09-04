"""Run-based connected components — bit-identical to the ITK label path.

``through`` and ``maxvol`` no longer call ``sitk.ConnectedComponent``; they
label maximal x-runs and union-find over intervals instead (see the block
comment above ``_cc_count_runs`` in vox1/kernels.py). These tests pin that
rewrite to the behavior it replaced by reimplementing the OLD ITK-based
formulation here and asserting voxel-for-voxel equality across densities,
shapes, and degenerate extents.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.vox1 import kernels


def _itk_through(fg: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """``through`` as it was written against sitk.ConnectedComponent."""
    labels = sitk.GetArrayFromImage(
        sitk.ConnectedComponent(sitk.GetImageFromArray(fg) != 0, True)
    ).astype(np.uint32)
    max_label = int(labels.max(initial=0))
    flags = np.zeros(max_label + 1, dtype=np.uint8)
    selected = (seed != 0) & (labels > 0)
    if selected.any():
        flags[labels[selected]] = 1
    result = np.zeros(fg.shape, dtype=np.uint8)
    foreground = labels > 0
    result[foreground] = flags[labels[foreground]]
    return result


def _itk_maxvol(fg: np.ndarray) -> np.ndarray:
    """``maxvol`` as it was written against sitk.ConnectedComponent."""
    labels = sitk.GetArrayFromImage(
        sitk.ConnectedComponent(sitk.GetImageFromArray(fg) != 0, True)
    ).astype(np.uint32)
    max_label = int(labels.max(initial=0))
    if max_label <= 0:
        return np.zeros(fg.shape, dtype=np.uint8)
    volumes = np.bincount(labels.reshape(-1), minlength=max_label + 1)
    best = int(volumes[1:].max(initial=0))
    selected = np.zeros(max_label + 1, dtype=np.uint8)
    if best > 0:
        selected[1:] = (volumes[1:] == best).astype(np.uint8)
    return selected[labels].reshape(fg.shape)


def _blobby(shape: tuple[int, int, int], fraction: float, seed: int) -> np.ndarray:
    """A mask with realistically few, large components (unlike pure noise)."""
    values = np.random.default_rng(seed).random(shape).astype(np.float32)
    if min(shape) >= 4:  # the recursive gaussian needs 4 voxels per axis
        values = sitk.GetArrayFromImage(
            sitk.SmoothingRecursiveGaussian(sitk.GetImageFromArray(values), 2.0)
        )
    return (values >= np.quantile(values, 1.0 - fraction)).astype(np.uint8)


def _run(kernel, *arrays: np.ndarray) -> np.ndarray:
    return sitk.GetArrayFromImage(kernel(*(sitk.GetImageFromArray(a) for a in arrays)))


# Anisotropic and degenerate extents are included deliberately: the run
# decomposition indexes rows as z*ny+y and links four neighbour rows, so an
# off-by-one in that arithmetic only shows up when nz != ny != nx.
SHAPES = [(12, 15, 19), (1, 24, 24), (24, 1, 24), (24, 24, 1), (5, 5, 5), (2, 3, 4)]
FRACTIONS = [0.02, 0.1, 0.3, 0.5, 0.9]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("fraction", FRACTIONS)
def test_through_matches_itk_labelling(shape, fraction):
    fg = _blobby(shape, fraction, seed=0)
    seed = (np.random.default_rng(1).random(shape) < 0.05).astype(np.uint8)
    np.testing.assert_array_equal(_run(kernels.through, seed, fg),
                                  _itk_through(fg, seed))


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("fraction", FRACTIONS)
def test_maxvol_matches_itk_labelling(shape, fraction):
    fg = _blobby(shape, fraction, seed=0)
    np.testing.assert_array_equal(_run(kernels.maxvol, fg), _itk_maxvol(fg))


@pytest.mark.parametrize("probability", [0.3, 0.5, 0.7])
def test_uniform_noise_matches_itk_labelling(probability):
    """Salt-and-pepper is the adversarial case: run compression buys nothing
    and the component graph is maximally tangled, so any union-find or
    two-pointer defect surfaces here first."""
    rng = np.random.default_rng(7)
    shape = (10, 21, 33)
    fg = (rng.random(shape) < probability).astype(np.uint8)
    seed = (rng.random(shape) < 0.01).astype(np.uint8)
    np.testing.assert_array_equal(_run(kernels.through, seed, fg),
                                  _itk_through(fg, seed))
    np.testing.assert_array_equal(_run(kernels.maxvol, fg), _itk_maxvol(fg))


@pytest.mark.parametrize("fill", [0, 1])
def test_constant_volumes(fill):
    shape = (6, 7, 8)
    fg = np.full(shape, fill, dtype=np.uint8)
    seed = np.zeros(shape, dtype=np.uint8)
    seed[0, 0, 0] = 1
    np.testing.assert_array_equal(_run(kernels.through, seed, fg),
                                  _itk_through(fg, seed))
    np.testing.assert_array_equal(_run(kernels.maxvol, fg), _itk_maxvol(fg))


def test_maxvol_keeps_every_tied_component():
    """Two components of equal size must BOTH survive — the ITK path selects
    `volumes == best`, not argmax, and the run-based path has to agree."""
    fg = np.zeros((3, 3, 9), dtype=np.uint8)
    fg[1, 1, 1:3] = 1
    fg[1, 1, 6:8] = 1
    result = _run(kernels.maxvol, fg)
    np.testing.assert_array_equal(result, fg)
    np.testing.assert_array_equal(result, _itk_maxvol(fg))


def test_through_selects_only_seeded_components():
    fg = np.zeros((3, 3, 9), dtype=np.uint8)
    fg[1, 1, 1:3] = 1
    fg[1, 1, 6:8] = 1
    seed = np.zeros((3, 3, 9), dtype=np.uint8)
    seed[1, 1, 6] = 1
    expected = np.zeros_like(fg)
    expected[1, 1, 6:8] = 1
    np.testing.assert_array_equal(_run(kernels.through, seed, fg), expected)


def test_diagonal_voxels_are_one_component():
    """26-connectivity: corner-touching voxels join. This is the property the
    run-linking predicate (intervals overlapping after a one-voxel dilation)
    encodes, and the one an 6-/18-connected implementation would get wrong."""
    fg = np.zeros((3, 3, 3), dtype=np.uint8)
    fg[0, 0, 0] = 1
    fg[1, 1, 1] = 1
    fg[2, 2, 2] = 1
    seed = np.zeros((3, 3, 3), dtype=np.uint8)
    seed[0, 0, 0] = 1
    np.testing.assert_array_equal(_run(kernels.through, seed, fg), fg)
    np.testing.assert_array_equal(_run(kernels.maxvol, fg), fg)


def test_far_diagonal_voxels_stay_separate():
    fg = np.zeros((3, 3, 5), dtype=np.uint8)
    fg[0, 0, 0] = 1
    fg[2, 2, 4] = 1
    seed = np.zeros((3, 3, 5), dtype=np.uint8)
    seed[0, 0, 0] = 1
    expected = np.zeros_like(fg)
    expected[0, 0, 0] = 1
    np.testing.assert_array_equal(_run(kernels.through, seed, fg), expected)
