from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk

from tests.data_registry import write_deterministic_gray_pair
from voxlogica.primitives.vox1 import kernels


@pytest.mark.unit
def test_crosscorr_backend_defaults_to_numpy_without_numba(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOXLOGICA_VOX1_CROSSCORR_BACKEND", raising=False)
    monkeypatch.setattr(kernels, "_HAS_NUMBA", False)
    assert kernels._crosscorr_backend() == "numpy"


@pytest.mark.unit
def test_crosscorr_backend_numba_request_falls_back_to_python_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOXLOGICA_VOX1_CROSSCORR_BACKEND", "numba")
    monkeypatch.setattr(kernels, "_HAS_NUMBA", False)
    assert kernels._crosscorr_backend() == "python"


@pytest.mark.unit
def test_crosscorr_numpy_backend_matches_python_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = write_deterministic_gray_pair(
        tmp_path,
        seed=21,
        shape=(6, 7, 5),
        spacing=(0.7, 1.3, 2.1),
    )

    m1 = sitk.ReadImage(str(paths["gray1"]))
    m2 = sitk.ReadImage(str(paths["gray2"]))
    a = kernels.intensity(m1)
    b = kernels.intensity(m2)
    fb = kernels.tt()
    m1_scalar = kernels.min_value(b)
    m2_scalar = kernels.max_value(b)

    kernels.reset_runtime_state()
    _ = kernels.intensity(m1)
    monkeypatch.setenv("VOXLOGICA_VOX1_CROSSCORR_BACKEND", "python")
    python_out = kernels.crossCorrelation(1.0, a, b, fb, m1_scalar, m2_scalar, 8.0)

    kernels.reset_runtime_state()
    _ = kernels.intensity(m1)
    monkeypatch.setenv("VOXLOGICA_VOX1_CROSSCORR_BACKEND", "numpy")
    numpy_out = kernels.crossCorrelation(1.0, a, b, fb, m1_scalar, m2_scalar, 8.0)

    python_arr = sitk.GetArrayFromImage(python_out)
    numpy_arr = sitk.GetArrayFromImage(numpy_out)
    assert python_arr.shape == numpy_arr.shape
    assert np.allclose(python_arr, numpy_arr, atol=1e-6, rtol=1e-6, equal_nan=True)


@pytest.mark.unit
def test_crosscorr_numba_backend_matches_numpy_backend_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not kernels._HAS_NUMBA:
        pytest.skip("numba not available in this environment")

    paths = write_deterministic_gray_pair(
        tmp_path,
        seed=29,
        shape=(8, 9, 7),
        spacing=(0.7, 1.0, 1.5),
    )

    m1 = sitk.ReadImage(str(paths["gray1"]))
    m2 = sitk.ReadImage(str(paths["gray2"]))
    a = kernels.intensity(m1)
    b = kernels.intensity(m2)
    fb = kernels.tt()
    m1_scalar = kernels.min_value(b)
    m2_scalar = kernels.max_value(b)

    kernels.reset_runtime_state()
    _ = kernels.intensity(m1)
    monkeypatch.setenv("VOXLOGICA_VOX1_CROSSCORR_BACKEND", "numba")
    numba_out = kernels.crossCorrelation(2.0, a, b, fb, m1_scalar, m2_scalar, 12.0)

    kernels.reset_runtime_state()
    _ = kernels.intensity(m1)
    monkeypatch.setenv("VOXLOGICA_VOX1_CROSSCORR_BACKEND", "numpy")
    numpy_out = kernels.crossCorrelation(2.0, a, b, fb, m1_scalar, m2_scalar, 12.0)

    numba_arr = sitk.GetArrayFromImage(numba_out)
    numpy_arr = sitk.GetArrayFromImage(numpy_out)
    assert numba_arr.shape == numpy_arr.shape
    assert np.allclose(numba_arr, numpy_arr, atol=1e-6, rtol=1e-6, equal_nan=True)
