"""nnU-Net's training writer must accept volumetric data.

The prediction path has always taken 2D and 3D (`volumes_to_nnunet_array`
sorts the two apart itself), and `write_label` never restricted
dimensionality. Only `write_nifti` -- the training half -- rejected anything
that was not 2D, which made `3d_fullres` on a real dataset impossible to
express: a 240x240x155 FLAIR volume died with "expected 2D image data".

These tests pin the capability, the geometry it must preserve, and the
diagnostic that must survive: a MIS-DELIVERED value is still an error, and the
message still has to name what arrived. That message is the only thing that
distinguished a wrong sequence element from a kernel returning a scalar.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.primitives.nnunet import io


def test_writes_a_3d_sitk_image_and_keeps_its_geometry(tmp_path):
    array = np.random.rand(8, 16, 24).astype(np.float32)
    image = sitk.GetImageFromArray(array)
    image.SetSpacing((0.5, 1.0, 2.0))
    image.SetOrigin((3.0, 4.0, 5.0))

    io.write_nifti(image, tmp_path / "case_0000.nii.gz")
    written = sitk.ReadImage(str(tmp_path / "case_0000.nii.gz"))

    assert written.GetSize() == (24, 16, 8)
    assert written.GetSpacing() == pytest.approx((0.5, 1.0, 2.0))
    assert written.GetOrigin() == pytest.approx((3.0, 4.0, 5.0))
    assert np.allclose(sitk.GetArrayFromImage(written), array, atol=1e-6)


def test_writes_a_3d_numpy_array(tmp_path):
    array = np.random.rand(4, 5, 6).astype(np.float32)

    io.write_nifti(array, tmp_path / "case_0000.nii.gz")
    written = sitk.ReadImage(str(tmp_path / "case_0000.nii.gz"))

    assert written.GetSize() == (6, 5, 4)
    assert written.GetSpacing() == pytest.approx((1.0, 1.0, 1.0))


def test_still_writes_2d(tmp_path):
    io.write_nifti(np.random.rand(4, 5).astype(np.float32), tmp_path / "case_0000.nii.gz")
    assert sitk.ReadImage(str(tmp_path / "case_0000.nii.gz")).GetSize() == (5, 4)


def test_a_3d_label_volume_is_binarized(tmp_path):
    labels = (np.random.rand(4, 5, 6) > 0.5).astype(np.uint8) * 4

    sanitized = io.write_label(labels, tmp_path / "case.nii.gz")

    assert sanitized is True
    written = sitk.GetArrayFromImage(sitk.ReadImage(str(tmp_path / "case.nii.gz")))
    assert set(np.unique(written).tolist()) <= {0, 1}


def test_a_mis_delivered_string_is_still_an_error_that_names_itself(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        io.write_nifti("case_0", tmp_path / "case_0000.nii.gz")

    message = str(excinfo.value)
    assert "case_0000.nii.gz" in message      # which slot
    assert "shape ()" in message              # what shape arrived
    assert "str" in message and "case_0" in message   # and what it actually was


def test_a_four_dimensional_volume_is_refused(tmp_path):
    with pytest.raises(ValueError, match="2D or 3D"):
        io.write_nifti(np.zeros((2, 3, 4, 5), dtype=np.float32), tmp_path / "case_0000.nii.gz")
