"""Stencil fusion -- a neighbourhood op absorbed into a fused cone.

A cone member normally reads its inputs at the voxel being written. A stencil
member reads across a box around it, which forces the generated loop from flat
1D indexing to a real ``(z, y, x)`` nest -- a different CALLING CONVENTION, not
just a different loop body, since arrays must arrive with their axes intact.

Volumes only and deliberately awkward shapes: an axis of length 1 is where the
clamped boundary reads degenerate (every neighbour along that axis is the
centre voxel), and a mis-generated clamp shows up there first.

The correctness bar is bit-identity with the REAL kernel, not with some
independent reimplementation of what dilation "should" be. ``vox1.near`` is
not max-over-the-box: sitk.BinaryDilate copies its input and then sets dilated
voxels to the foreground value 1, so a voxel holding any other value is
neither foreground nor erased. See tests/unit/test_vox1_near.py.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.engine.numba_fusion import ArgRef, ConeShape, _generate_source, compile_shape
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.primitives.vox1 import kernels


@pytest.fixture(scope="module")
def registry() -> PrimitiveRegistry:
    return PrimitiveRegistry()


NEAR_ONLY = ConeShape(
    ops=("vox1.near",),
    arg_refs=((ArgRef("array_input", 0),),),
    array_input_count=1,
    scalar_input_count=0,
    out_positions=(0,),
    spatial=True,
)

# near(arr0) & arr1 -- the shape stencil fusion exists to build: a stencil seed
# with an elementwise consumer grown onto it, so the intermediate volume is
# never materialized at all.
NEAR_AND = ConeShape(
    ops=("vox1.near", "vox1.and"),
    arg_refs=(
        (ArgRef("array_input", 0),),
        (ArgRef("member", 0), ArgRef("array_input", 1)),
    ),
    array_input_count=2,
    scalar_input_count=0,
    out_positions=(1,),
    spatial=True,
)


@pytest.fixture(scope="module")
def near_kernel(registry):
    return compile_shape(NEAR_ONLY, registry)


@pytest.fixture(scope="module")
def near_and_kernel(registry):
    return compile_shape(NEAR_AND, registry)


def _real_near(volume: np.ndarray) -> np.ndarray:
    return sitk.GetArrayFromImage(kernels.near(sitk.GetImageFromArray(volume)))


SHAPES = [(6, 7, 8), (5, 5, 5), (1, 8, 8), (8, 1, 8), (8, 8, 1),
          (1, 1, 1), (2, 2, 2), (20, 17, 13), (31, 4, 29)]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("density", [0.0, 0.05, 0.3, 0.9, 1.0])
def test_fused_near_matches_real_kernel(near_kernel, shape, density):
    rng = np.random.default_rng(abs(hash((shape, density))) % (2**32))
    volume = np.ascontiguousarray((rng.random(shape) < density).astype(np.uint8))
    out = np.empty(shape, np.uint8)
    near_kernel(volume, out)
    np.testing.assert_array_equal(out, _real_near(volume))


@pytest.mark.parametrize("shape", [(4, 5, 6), (12, 12, 12)])
def test_fused_near_passes_through_non_foreground(near_kernel, shape):
    """Values other than 0/1 are neither dilated nor erased."""
    rng = np.random.default_rng(abs(hash(shape)) % (2**32))
    volume = np.ascontiguousarray(rng.integers(0, 7, shape).astype(np.uint8))
    out = np.empty(shape, np.uint8)
    near_kernel(volume, out)
    np.testing.assert_array_equal(out, _real_near(volume))


@pytest.mark.parametrize("shape", [(6, 7, 8), (5, 5, 5), (1, 9, 9), (20, 17, 13)])
@pytest.mark.parametrize("density", [0.05, 0.3, 0.9])
def test_fused_stencil_plus_consumer(near_and_kernel, shape, density):
    rng = np.random.default_rng(abs(hash((shape, density, "and"))) % (2**32))
    a = np.ascontiguousarray((rng.random(shape) < density).astype(np.uint8))
    b = np.ascontiguousarray((rng.random(shape) < 0.5).astype(np.uint8))
    out = np.empty(shape, np.uint8)
    near_and_kernel(a, b, out)
    np.testing.assert_array_equal(out, _real_near(a) & b)


def test_generated_source_is_a_three_deep_nest(registry):
    """The spatial path must not silently fall back to the flat generator.

    A flat kernel handed 3D arrays would not fail loudly -- ``arr0.shape[0]``
    is the z extent, so it would loop over slices and write a fraction of the
    volume. Pinning the loop structure catches that directly.
    """
    source = _generate_source(NEAR_ONLY, registry)
    assert "for _z in range(nz):" in source
    assert "for _y in range(ny):" in source
    assert "for _x in range(nx):" in source
    assert "arr0.shape[0]" not in source


def test_flat_path_unchanged_for_elementwise_cones(registry):
    """Non-spatial cones keep the pre-flattened 1D convention."""
    flat = ConeShape(
        ops=("vox1.not",),
        arg_refs=((ArgRef("array_input", 0),),),
        array_input_count=1,
        scalar_input_count=0,
        out_positions=(0,),
    )
    assert flat.spatial is False
    source = _generate_source(flat, registry)
    assert "n = arr0.shape[0]" in source
    assert "for _i in range(n):" in source
    assert "_z" not in source


def test_spatial_is_part_of_the_cache_key():
    """Two shapes differing only in convention must not share a compilation."""
    spatial = ConeShape(
        ops=("vox1.not",),
        arg_refs=((ArgRef("array_input", 0),),),
        array_input_count=1,
        scalar_input_count=0,
        out_positions=(0,),
        spatial=True,
    )
    flat = ConeShape(
        ops=("vox1.not",),
        arg_refs=((ArgRef("array_input", 0),),),
        array_input_count=1,
        scalar_input_count=0,
        out_positions=(0,),
        spatial=False,
    )
    assert spatial != flat
    assert hash(spatial) != hash(flat) or spatial != flat


def test_near_is_registered_as_a_stencil(registry):
    spec = registry.get_spec("vox1.near")
    assert spec.stencil is not None
    assert spec.stencil.radius == 1
    assert spec.elementwise is None, "near must not also be elementwise: it would fuse unsoundly"
