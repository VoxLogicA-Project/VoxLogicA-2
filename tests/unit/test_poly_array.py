"""PolyArray view laziness, zero-copy guarantees, and byte accounting.

See ``voxlogica.arrays`` for the invariants under test: sitk->numpy is a
zero-copy read-only alias; numpy->sitk always copies; nbytes reflects every
resident view, not just one.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.arrays import Geometry, PolyArray


def _sitk_image(shape=(4, 5, 6), spacing=(1.0, 2.0, 0.5), value=3) -> sitk.Image:
    arr = np.full(shape, value, dtype=np.uint8)
    image = sitk.GetImageFromArray(arr)
    image.SetSpacing(spacing)
    image.SetOrigin((1.0, 1.0, 1.0))
    image.SetDirection((1, 0, 0, 0, 1, 0, 0, 0, 1))
    return image


@pytest.mark.unit
def test_from_sitk_np_view_is_zero_copy() -> None:
    """The numpy view built from a sitk image must alias the same buffer."""
    image = _sitk_image()
    poly = PolyArray.from_sitk(image)
    arr = poly.np()
    direct = sitk.GetArrayViewFromImage(image)
    assert np.shares_memory(arr, direct), "np() must not copy the sitk buffer"
    assert poly.is_readonly_np


@pytest.mark.unit
def test_from_sitk_defers_np_view_until_requested() -> None:
    """Laziness: constructing from sitk must not build the numpy view eagerly."""
    poly = PolyArray.from_sitk(_sitk_image())
    assert poly.resident_views() == ("sitk",)
    poly.np()
    assert set(poly.resident_views()) == {"sitk", "np"}


@pytest.mark.unit
def test_from_numpy_sitk_view_copies() -> None:
    """numpy -> sitk cannot be zero-copy (SimpleITK owns its buffers)."""
    arr = np.full((4, 5, 6), 7, dtype=np.uint8)
    poly = PolyArray.from_numpy(arr, Geometry.identity(3))
    image = poly.sitk()
    round_trip = sitk.GetArrayViewFromImage(image)
    assert not np.shares_memory(arr, round_trip), "np->sitk must copy"
    assert np.array_equal(arr, round_trip)
    assert not poly.is_readonly_np, "the original numpy view stays writable"


@pytest.mark.unit
def test_geometry_round_trips_sitk_to_poly_to_sitk() -> None:
    image = _sitk_image(spacing=(1.5, 2.5, 0.25))
    poly = PolyArray.from_sitk(image)
    rebuilt = poly.sitk()
    assert rebuilt.GetSpacing() == image.GetSpacing()
    assert rebuilt.GetOrigin() == image.GetOrigin()
    assert rebuilt.GetDirection() == image.GetDirection()
    assert np.array_equal(sitk.GetArrayFromImage(rebuilt), sitk.GetArrayFromImage(image))


@pytest.mark.unit
def test_nbytes_counts_one_buffer_for_sitk_only_value() -> None:
    image = _sitk_image(shape=(4, 5, 6))
    poly = PolyArray.from_sitk(image)
    expected = 4 * 5 * 6 * np.dtype(np.uint8).itemsize
    assert poly.nbytes == expected


@pytest.mark.unit
def test_nbytes_counts_two_buffers_once_sitk_is_built_from_numpy() -> None:
    """A from_numpy value that later builds a sitk view holds two independent
    buffers (the copy), and both must be counted — this is what proactive
    reclaim and admission accounting rely on to see the true resident cost."""
    arr = np.full((4, 5, 6), 1, dtype=np.uint8)
    poly = PolyArray.from_numpy(arr)
    one_buffer = poly.nbytes
    poly.sitk()
    assert poly.nbytes == one_buffer * 2


@pytest.mark.unit
def test_dlpack_round_trips_through_torch() -> None:
    torch = pytest.importorskip("torch")
    arr = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    poly = PolyArray.from_numpy(arr)
    tensor = torch.from_dlpack(poly)
    assert tensor.shape == (2, 3, 4)
    assert torch.equal(tensor, torch.from_numpy(arr))


@pytest.mark.unit
def test_release_view_drops_cache_and_readonly_flag() -> None:
    poly = PolyArray.from_sitk(_sitk_image())
    poly.np()
    assert "np" in poly.resident_views()
    poly.release_view("np")
    assert "np" not in poly.resident_views()
    assert not poly.is_readonly_np
