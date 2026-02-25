"""Canonical paths for repository test data assets."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import SimpleITK as sitk


TESTS_ROOT = Path(__file__).resolve().parent
TEST_DATA_ROOT = TESTS_ROOT / "data"
CHRIS_T1 = TEST_DATA_ROOT / "chris_t1.nii.gz"


def get_test_data_path(name: str) -> Path:
    """Resolve a test data asset under `tests/data`."""
    path = TEST_DATA_ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"Test data asset not found: {path}")
    return path


def write_deterministic_gray_pair(
    root: Path,
    *,
    seed: int = 13,
    shape: tuple[int, int, int] = (28, 48, 48),
    spacing: tuple[float, float, float] = (0.9, 1.1, 1.4),
) -> dict[str, Path]:
    """Write a deterministic grayscale image pair and return their paths."""
    root.mkdir(parents=True, exist_ok=True)
    gray1_path = root / "gray1.nii.gz"
    gray2_path = root / "gray2.nii.gz"

    rng = np.random.default_rng(seed)
    base = rng.normal(loc=90.0, scale=25.0, size=shape).astype(np.float32)
    second = (np.roll(base, shift=2, axis=0) * 0.82 + 11.0).astype(np.float32)

    img1 = sitk.GetImageFromArray(base, isVector=False)
    img2 = sitk.GetImageFromArray(second, isVector=False)
    img1.SetSpacing(spacing)
    img2.CopyInformation(img1)

    sitk.WriteImage(img1, str(gray1_path))
    sitk.WriteImage(img2, str(gray2_path))
    return {"gray1": gray1_path, "gray2": gray2_path}


def write_deterministic_color_sample(root: Path) -> Path:
    """Write a deterministic RGB image sample and return the path."""
    root.mkdir(parents=True, exist_ok=True)
    color_path = root / "color.png"
    color = np.zeros((9, 11, 3), dtype=np.uint8)
    color[..., 0] = np.linspace(0, 255, 11, dtype=np.uint8)
    color[..., 1] = np.linspace(255, 0, 9, dtype=np.uint8)[:, None]
    color[..., 2] = (
        (color[..., 0].astype(np.uint16) + color[..., 1].astype(np.uint16)) // 2
    ).astype(np.uint8)
    color_img = sitk.GetImageFromArray(color, isVector=True)
    sitk.WriteImage(color_img, str(color_path))
    return color_path
