"""Regression tests for NumPy kernels writing fresh SimpleITK outputs."""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.arrays import WritableViewUnavailable, allocate_writable_like
from voxlogica.primitives.vox1 import kernels


def _image(array: np.ndarray) -> sitk.Image:
    image = sitk.GetImageFromArray(array)
    image.SetSpacing((0.7, 1.3))
    image.SetOrigin((2.0, -1.0))
    return image


def _assert_same(actual: sitk.Image, expected: sitk.Image) -> None:
    assert actual.GetSize() == expected.GetSize()
    assert actual.GetSpacing() == expected.GetSpacing()
    assert actual.GetOrigin() == expected.GetOrigin()
    assert actual.GetDirection() == expected.GetDirection()
    assert np.array_equal(
        sitk.GetArrayViewFromImage(actual),
        sitk.GetArrayViewFromImage(expected),
        equal_nan=True,
    )


def test_fresh_writable_alias_updates_image_and_pins_owner() -> None:
    reference = _image(np.zeros((3, 4), dtype=np.float32))
    image, view = allocate_writable_like(reference, sitk.sitkFloat32)

    view[...] = 7.5

    assert view._src is image
    assert np.array_equal(sitk.GetArrayViewFromImage(image), np.full((3, 4), 7.5, dtype=np.float32))
    assert image.GetSpacing() == reference.GetSpacing()
    assert image.GetOrigin() == reference.GetOrigin()


def test_writable_alias_rejects_vector_images() -> None:
    vector = sitk.GetImageFromArray(np.zeros((3, 4, 2), dtype=np.float32), isVector=True)
    with pytest.raises(WritableViewUnavailable, match="vector"):
        allocate_writable_like(vector, sitk.sitkUInt8)


def test_native_output_can_be_disabled_without_changing_results(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _image(np.array([[0.0, 1.0, np.nan]], dtype=np.float32))
    monkeypatch.setenv("VOXLOGICA_WRITABLE_SITK_OUTPUT", "off")

    _assert_same(kernels.geq_sv(1.0, image), sitk.GreaterEqual(image, 1.0))


def test_required_mode_fails_instead_of_silently_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = _image(np.array([[0.0, 1.0]], dtype=np.float32))
    monkeypatch.setenv("VOXLOGICA_WRITABLE_SITK_OUTPUT", "required")

    def unavailable(_reference: sitk.Image, _pixel_id: int):
        raise WritableViewUnavailable("probe failed")

    monkeypatch.setattr(kernels, "allocate_writable_like", unavailable)
    with pytest.raises(WritableViewUnavailable, match="probe failed"):
        kernels.geq_sv(1.0, image)


def test_native_through_and_border_match_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    components = _image(
        np.array(
            [
                [1, 1, 0, 0, 1, 1],
                [1, 1, 0, 0, 1, 1],
                [0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        )
    )
    intersection = _image(
        np.array(
            [
                [1, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        )
    )

    monkeypatch.setenv("VOXLOGICA_WRITABLE_SITK_OUTPUT", "off")
    expected_through = kernels.through(intersection, components)
    expected_border = kernels.border(components)
    monkeypatch.setenv("VOXLOGICA_WRITABLE_SITK_OUTPUT", "required")

    _assert_same(kernels.through(intersection, components), expected_through)
    _assert_same(kernels.border(components), expected_border)


def test_native_maxvol_and_percentiles_match_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    values = _image(
        np.array(
            [
                [4.0, 1.0, 1.0, 2.0, 8.0, 3.0],
                [4.0, 1.0, 7.0, 2.0, 8.0, 3.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ],
            dtype=np.float32,
        )
    )
    mask = _image(
        np.array(
            [
                [1, 1, 0, 1, 1, 1],
                [1, 1, 0, 1, 1, 1],
                [0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        )
    )

    monkeypatch.setenv("VOXLOGICA_WRITABLE_SITK_OUTPUT", "off")
    expected_maxvol = kernels.maxvol(mask)
    expected_percentiles = kernels.percentiles(values, mask, 0.5)
    monkeypatch.setenv("VOXLOGICA_WRITABLE_SITK_OUTPUT", "required")

    _assert_same(kernels.maxvol(mask), expected_maxvol)
    _assert_same(kernels.percentiles(values, mask, 0.5), expected_percentiles)


def test_native_output_kernels_match_simpleitk_on_nan_and_boundaries() -> None:
    values = _image(np.array([[np.nan, -1.0, 0.0, 1.0, 3.0, 5.0]], dtype=np.float32))
    boolean = _image(np.array([[0, 1, 2, 5, 255, 0]], dtype=np.uint8))
    mask = _image(np.array([[0, 1, 1, 0, 1, 1]], dtype=np.uint8))

    cases = (
        (kernels.logical_not(boolean), sitk.Not(boolean)),
        (kernels.logical_and(boolean, True), sitk.And(boolean, True)),
        (kernels.logical_or(boolean, False), sitk.Or(boolean, False)),
        (kernels.eq_sv(1.0, values), sitk.BinaryThreshold(values, 1.0, 1.0, 1, 0)),
        (kernels.geq_sv(1.0, values), sitk.GreaterEqual(values, 1.0)),
        (kernels.leq_sv(1.0, values), sitk.LessEqual(values, 1.0)),
        (kernels.between(1.0, 3.0, values), sitk.BinaryThreshold(values, 1.0, 3.0, 1, 0)),
        (kernels.equal(values, 1.0), sitk.Equal(values, 1.0)),
        (kernels.not_equal(values, 1.0), sitk.NotEqual(values, 1.0)),
        (kernels.less(values, 1.0), sitk.Less(values, 1.0)),
        (kernels.less_equal(values, 1.0), sitk.LessEqual(values, 1.0)),
        (kernels.greater(values, 1.0), sitk.Greater(values, 1.0)),
        (kernels.greater_equal(values, 1.0), sitk.GreaterEqual(values, 1.0)),
        (kernels.greater(1.0, values), sitk.Less(values, 1.0)),
        (kernels.mask(values, mask), sitk.Mask(values, mask, 0.0)),
    )
    for actual, expected in cases:
        assert isinstance(actual, sitk.Image)
        _assert_same(actual, expected)
