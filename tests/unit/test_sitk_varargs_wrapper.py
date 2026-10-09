"""SimpleITK procedures declared (*args, **kwargs) receive every argument.

The wrapper used to walk the two declared parameters by position, so a call
such as Extract(img, size, index) or RescaleIntensity(img, 0, 65535) lost
everything after its second argument and fell back to SimpleITK's defaults.
"""

import numpy as np
import SimpleITK as sitk

from voxlogica.primitives.simpleitk.runtime import _wrap_sitk_function


def _call(name, *args):
    wrapped = _wrap_sitk_function(getattr(sitk, name), name)
    return wrapped(**{str(i): a for i, a in enumerate(args)})


def _volume():
    arr = np.arange(4 * 3 * 2, dtype=np.float32).reshape(4, 3, 2)  # z, y, x
    return sitk.GetImageFromArray(arr), arr


def test_extract_uses_the_index():
    img, arr = _volume()
    out = _call("Extract", img, [2, 3, 0], [0, 0, 2])
    np.testing.assert_array_equal(sitk.GetArrayFromImage(out), arr[2])


def test_rescale_intensity_uses_the_maximum():
    img, _ = _volume()
    out = _call("RescaleIntensity", img, 0.0, 65535.0)
    stats = sitk.MinimumMaximumImageFilter()
    stats.Execute(out)
    assert stats.GetMinimum() == 0.0
    assert stats.GetMaximum() == 65535.0
