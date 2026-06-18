from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.geom.blank import execute as blank
from voxlogica.primitives.geom.circle import execute as circle
from voxlogica.primitives.geom.regular_polygon import execute as regular_polygon


@pytest.mark.unit
def test_blank_creates_2d_canvas() -> None:
    image = blank(**{"0": 32, "1": 48, "2": 5})
    assert image.GetDimension() == 2
    array = sitk.GetArrayFromImage(image)
    assert array.shape == (32, 48)
    assert float(array.max()) == 5.0


@pytest.mark.unit
def test_circle_draws_without_mutating_input() -> None:
    base = blank(**{"0": 64, "1": 64, "2": 0})
    before = sitk.GetArrayFromImage(base).copy()
    drawn = circle(**{"0": base, "1": 32, "2": 32, "3": 10, "4": 200})
    after = sitk.GetArrayFromImage(base)

    assert np.array_equal(before, after)
    array = sitk.GetArrayFromImage(drawn)
    assert float(array[32, 32]) == 200.0
    assert float(array[0, 0]) == 0.0


@pytest.mark.unit
def test_regular_polygon_draws_square() -> None:
    base = blank(**{"0": 64, "1": 64, "2": 0})
    drawn = regular_polygon(**{"0": base, "1": 32, "2": 32, "3": 16, "4": 4, "5": 150})
    array = sitk.GetArrayFromImage(drawn)
    assert float(array[32, 32]) == 150.0
    assert float(array[0, 0]) == 0.0
