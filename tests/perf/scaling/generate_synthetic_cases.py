"""Generate a small, fully synthetic BraTS-shaped dataset for the engine scaling
study (manuscripts/engine-scaling-2026-07.md).

Why synthetic: the real study ran against BraTS2020 (2.7 GB, not part of this
repo, and under a data-use agreement). Nobody without that dataset could
reproduce the measurements or verify the scripts actually work. This generator
needs nothing but numpy + SimpleITK (both already required by the engine) and
is fully deterministic -- no RNG, no seed to record -- so the same command
always produces byte-identical volumes.

What it reproduces and what it does NOT:
  - SAME per-volume shape/dtype as the real dataset (240x240x155 float32), so
    memory traffic and cache behaviour are representative.
  - SAME downstream primitive graph: bench_scaling.imgql in this directory
    calls the actual brain_mask/preprocess_flair/percentiles vox1 primitives
    on these volumes, unmodified from the real pipeline -- so the operator MIX
    (see manuscript sec on the 62%-never-unpack boolean majority) is identical
    in kind, not just similar.
  - It does NOT reproduce real anatomy, and a Dice score computed against it
    means nothing clinically. This is a performance harness, not a validation
    one -- see [[brats-segmentation-findings]] and friends for the actual
    science, which needs the real dataset and is a completely separate line of
    work from this performance study.

Each case is one Gaussian blob (a synthetic "lesion") at a case-specific
center/sigma inside a synthetic "brain" ellipsoid, both closed-form functions
of the case index -- deterministic, no file I/O for the formula itself, cheap
to regenerate.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import SimpleITK as sitk

# Matches the real dataset's volume shape (see experiment.imgql), so memory
# traffic and cache pressure are representative of the actual study.
SHAPE = (155, 240, 240)  # SimpleITK/numpy axis order: (z, y, x)


def _blob(shape: tuple[int, int, int], center: tuple[float, float, float],
          sigma: float) -> np.ndarray:
    zz, yy, xx = np.meshgrid(
        np.arange(shape[0]), np.arange(shape[1]), np.arange(shape[2]),
        indexing="ij")
    d2 = ((zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2)
    return np.exp(-d2 / (2.0 * sigma * sigma)).astype(np.float32)


def make_case(index: int, shape: tuple[int, int, int] = SHAPE
              ) -> tuple[sitk.Image, sitk.Image]:
    """Return (flair, seg) for one synthetic case, a pure function of index."""
    cz, cy, cx = (s / 2.0 for s in shape)
    brain_sigma = min(shape) * 0.32
    brain = _blob(shape, (cz, cy, cx), brain_sigma)

    # Case-specific lesion: walk the center and sigma deterministically with
    # the index so different cases are genuinely different volumes, not
    # copies -- exercises the cache the same way N distinct real cases would.
    rng_free = index * 2654435761 % (1 << 32)  # deterministic mix, NOT a seed
    frac = (rng_free % 1000) / 1000.0
    offset = (frac - 0.5) * min(shape) * 0.3
    lesion_center = (cz + offset, cy - offset * 0.6, cx + offset * 0.3)
    lesion_sigma = min(shape) * (0.05 + 0.03 * ((rng_free // 1000) % 7) / 7.0)
    lesion = _blob(shape, lesion_center, lesion_sigma)

    flair_arr = np.clip(brain * 0.4 + lesion * 0.9, 0.0, 1.0).astype(np.float32)
    seg_arr = (lesion > 0.5).astype(np.uint8)

    flair = sitk.GetImageFromArray(flair_arr)
    seg = sitk.GetImageFromArray(seg_arr)
    return flair, seg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--cases", type=int, default=4,
                         help="Number of synthetic cases (default: 4, matching "
                              "the original _bench_scaling.imgql).")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(args.cases):
        case_dir = args.out_dir / f"Synthetic_{i:03d}"
        case_dir.mkdir(exist_ok=True)
        flair, seg = make_case(i)
        sitk.WriteImage(flair, str(case_dir / f"Synthetic_{i:03d}_flair.nii.gz"))
        sitk.WriteImage(seg, str(case_dir / f"Synthetic_{i:03d}_seg.nii.gz"))
    print(f"Wrote {args.cases} synthetic cases to {args.out_dir}")


if __name__ == "__main__":
    main()
