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


def _sitk_image_from_volume(value: Any) -> Any:
    """Return a SimpleITK image for a numpy array or existing SimpleITK image."""
    np, _nib = _require_numpy_nibabel()
    import SimpleITK as sitk  # type: ignore

    if isinstance(value, sitk.Image):
        return value
    array = np.asarray(value)
    if array.ndim != 2:
        raise ValueError(f"expected 2D image data, got shape {array.shape}")
    image = sitk.GetImageFromArray(array.astype(np.float32))
    image.SetSpacing((1.0, 1.0))
    image.SetOrigin((0.0, 0.0))
    return image


def volumes_to_nnunet_array(volumes: list[Any]) -> tuple[Any, dict[str, Any]]:
    """Convert VoxLogicA modality volumes to nnU-Net numpy input and properties."""
    np, _nib = _require_numpy_nibabel()
    import SimpleITK as sitk  # type: ignore

    images: list[Any] = []
    spacings: list[tuple[float, ...]] = []
    origins: list[tuple[float, ...]] = []
    directions: list[tuple[float, ...]] = []
    spacings_for_nnunet: list[list[float]] = []

    for value in volumes:
        itk_image = _sitk_image_from_volume(value)
        spacing = itk_image.GetSpacing()
        spacings.append(spacing)
        origins.append(itk_image.GetOrigin())
        directions.append(itk_image.GetDirection())
        npy_image = sitk.GetArrayFromImage(itk_image)
        if npy_image.ndim == 2:
            npy_image = npy_image[None, None]
            max_spacing = max(spacing)
            spacings_for_nnunet.append([max_spacing * 999, *list(spacing)[::-1]])
        elif npy_image.ndim == 3:
            npy_image = npy_image[None]
            spacings_for_nnunet.append(list(spacing)[::-1])
        else:
            raise ValueError(f"unexpected image dimensionality: {npy_image.ndim}")

        spacings_for_nnunet[-1] = [float(abs(item)) for item in spacings_for_nnunet[-1]]
        images.append(npy_image)

    shapes = [image.shape for image in images]
    if len(set(shapes)) != 1:
        raise ValueError(f"all modality volumes must share shape, got {shapes}")
    if len(set(spacings)) != 1:
        raise ValueError(f"all modality volumes must share spacing, got {spacings}")

    properties = {
        "sitk_stuff": {
            "spacing": spacings[0],
            "origin": origins[0],
            "direction": directions[0],
        },
        "spacing": spacings_for_nnunet[0],
    }
    return np.vstack(images, dtype=np.float32, casting="unsafe"), properties


def segmentation_to_sitk(segmentation: Any, properties: dict[str, Any]) -> Any:
    """Convert an nnU-Net segmentation array back to a SimpleITK image."""
    np, _nib = _require_numpy_nibabel()
    import SimpleITK as sitk  # type: ignore

    seg = np.asarray(segmentation)
    if seg.ndim == 3:
        output_dimension = len(properties["sitk_stuff"]["spacing"])
        if output_dimension == 2:
            seg = seg[0]
    image = sitk.GetImageFromArray(seg.astype(np.uint8 if np.max(seg) < 255 else np.uint16, copy=False))
    image.SetSpacing(properties["sitk_stuff"]["spacing"])
    image.SetOrigin(properties["sitk_stuff"]["origin"])
    image.SetDirection(properties["sitk_stuff"]["direction"])
    return image


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
