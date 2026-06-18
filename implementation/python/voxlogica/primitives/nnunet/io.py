"""NIfTI materialization helpers for nnUNet datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _require_numpy_nibabel():
    try:
        import nibabel as nib  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"nnUNet materialization requires nibabel and numpy: {exc}") from exc
    return np, nib


def _to_array(value: Any) -> tuple[Any, Any | None]:
    np, _nib = _require_numpy_nibabel()
    try:
        import SimpleITK as sitk  # type: ignore

        if isinstance(value, sitk.Image):
            return sitk.GetArrayFromImage(value), value
    except Exception:  # noqa: BLE001
        pass
    return np.asarray(value), None


def write_nifti(value: Any, destination: Path) -> None:
    """Write a 2D numpy/SimpleITK volume as nnUNet-compatible NIfTI."""
    np, _nib = _require_numpy_nibabel()
    import SimpleITK as sitk  # type: ignore

    destination.parent.mkdir(parents=True, exist_ok=True)
    array, reference = _to_array(value)
    if array.ndim != 2:
        raise ValueError(f"expected 2D image data, got shape {array.shape}")

    image = sitk.GetImageFromArray(array.astype(np.float32))
    if reference is not None:
        image.CopyInformation(reference)
    else:
        image.SetSpacing((1.0, 1.0))
        image.SetOrigin((0.0, 0.0))
    sitk.WriteImage(image, str(destination))


def write_label(value: Any, destination: Path) -> bool:
    """Write a label volume, binarizing non-{0,1} values when needed."""
    np, _nib = _require_numpy_nibabel()
    import SimpleITK as sitk  # type: ignore

    array, reference = _to_array(value)
    unique = sorted({int(v) for v in np.unique(array).tolist()})
    sanitized = any(v not in (0, 1) for v in unique)
    if sanitized:
        array = (array > 0).astype(np.uint8)
    else:
        array = array.astype(np.uint8)

    destination.parent.mkdir(parents=True, exist_ok=True)
    image = sitk.GetImageFromArray(array)
    if reference is not None:
        image.CopyInformation(reference)
    sitk.WriteImage(image, str(destination))
    return sanitized
