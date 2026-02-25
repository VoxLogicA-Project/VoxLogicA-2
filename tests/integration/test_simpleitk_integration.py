from __future__ import annotations

import pytest


@pytest.mark.integration
def test_simpleitk_direct_threshold_pipeline(tmp_path, sample_image_path):
    sitk = pytest.importorskip("SimpleITK")

    image = sitk.ReadImage(str(sample_image_path))
    assert image.GetDimension() in (2, 3)

    thresholded = sitk.BinaryThreshold(
        image,
        lowerThreshold=0,
        upperThreshold=100,
        insideValue=255,
        outsideValue=0,
    )

    stats = sitk.StatisticsImageFilter()
    stats.Execute(thresholded)
    assert stats.GetMinimum() >= 0
    assert stats.GetMaximum() <= 255

    png_output = tmp_path / "threshold.png"
    nii_output = tmp_path / "threshold.nii.gz"

    if thresholded.GetDimension() == 3:
        z = thresholded.GetSize()[2] // 2
        sitk.WriteImage(thresholded[:, :, z], str(png_output))
    else:
        sitk.WriteImage(thresholded, str(png_output))

    sitk.WriteImage(thresholded, str(nii_output))
    assert png_output.exists()
    assert nii_output.exists()
