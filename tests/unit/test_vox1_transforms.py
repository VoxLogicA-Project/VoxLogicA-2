from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.vox1.kernels import contralateral_asymmetry


@pytest.mark.unit
def test_contralateral_asymmetry_is_positive_and_axis_correct():
    values = np.zeros((3, 3, 7), dtype=np.float32)
    values[1, 1, 1] = 8.0
    image = sitk.GetImageFromArray(values)

    result = sitk.GetArrayFromImage(contralateral_asymmetry(image, 0.0))

    assert result[1, 1, 1] == 8.0
    assert result[1, 1, 5] == 0.0
    assert np.count_nonzero(result) == 1


@pytest.mark.unit
def test_contralateral_asymmetry_rejects_negative_sigma():
    image = sitk.GetImageFromArray(np.zeros((3, 3, 3), dtype=np.float32))
    with pytest.raises(ValueError, match="sigma_mm"):
        contralateral_asymmetry(image, -0.1)
