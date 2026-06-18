"""Shared helpers for functional geometry drawing primitives."""

from __future__ import annotations

from typing import Any

import numpy as np
import SimpleITK as sitk


def as_image(value: Any, name: str) -> sitk.Image:
    if not isinstance(value, sitk.Image):
        raise ValueError(f"{name} must be a SimpleITK Image, got {type(value).__name__}")
    if value.GetDimension() != 2:
        raise ValueError(f"{name} must be a 2D image, got dimension {value.GetDimension()}")
    return value


def as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} must be int-like: {value!r}") from exc


def as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} must be numeric: {value!r}") from exc


def copy_array(image: sitk.Image) -> np.ndarray:
    return np.asarray(sitk.GetArrayFromImage(image)).copy()


def image_from_array(array: np.ndarray, reference: sitk.Image) -> sitk.Image:
    result = sitk.GetImageFromArray(np.asarray(array))
    result.CopyInformation(reference)
    return result


def inside_regular_polygon(dx: np.ndarray, dy: np.ndarray, radius: float, sides: int) -> np.ndarray:
    if sides < 3:
        raise ValueError("regular polygon requires at least 3 sides")
    sector = 2.0 * np.pi / float(sides)
    angle = np.arctan2(dy, dx)
    folded = np.mod(angle + np.pi, sector) - (sector / 2.0)
    radial = np.hypot(dx, dy)
    limit = radius * np.cos(sector / 2.0) / np.maximum(np.cos(folded), 1e-9)
    return radial <= limit
