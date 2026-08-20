#!/usr/bin/env python3
"""Cut the axial planes out of a comparison volume written by tropical_slices.imgql.

The engine writes the whole 3D comparison as one NIfTI rather than three 2D
images, for a reason worth repeating: SimpleITK's Extract returns an EMPTY image
for a 3D-to-2D axial cut, silently -- a plane holding 13583 voxels came back
with volume 0. So the cut happens here, in numpy, where it can be checked.

    python cut_planes.py vol33 --planes 40,72,104 --cases 30,38 --suffix csf_vs_gt

Writes one PNG per (case, plane), plus a contact sheet per case with the three
planes side by side, which is what a reviewer actually looks at. Grey levels
come straight from tsr_compare: dim background, 85 = first mask only,
170 = second mask only, 255 = both. They are recoloured here -- red, blue,
white -- because three greys are hard to tell apart and three hues are not.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import SimpleITK as sitk
from PIL import Image


def colourise(plane: np.ndarray) -> np.ndarray:
    """Map tsr_compare's grey levels onto three hues over a grey background."""
    h, w = plane.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    bg = plane < 80                      # anatomy, dim
    a_only = (plane >= 80) & (plane < 130)
    b_only = (plane >= 130) & (plane < 220)
    both = plane >= 220
    grey = np.clip(plane * 2.2, 0, 200).astype(np.uint8)
    for c in range(3):
        rgb[..., c] = np.where(bg, grey, 0)
    rgb[a_only] = (235, 60, 60)          # first mask alone
    rgb[b_only] = (60, 110, 235)         # second mask alone
    rgb[both] = (255, 255, 255)          # both
    return rgb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("indir", help="directory holding the written volumes")
    ap.add_argument("--cases", required=True, help="comma separated case numbers")
    ap.add_argument("--suffix", required=True, help="volume name, e.g. csf_vs_gt")
    ap.add_argument("--planes", required=True,
                    help="comma separated axial indices, or per-case groups "
                         "separated by ';' in the same order as --cases")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    indir = Path(args.indir)
    outdir = Path(args.outdir or indir / "png")
    outdir.mkdir(parents=True, exist_ok=True)

    cases = [int(c) for c in args.cases.split(",")]
    groups = args.planes.split(";")
    if len(groups) == 1:
        groups = groups * len(cases)

    for case, group in zip(cases, groups):
        path = indir / f"c{case}_{args.suffix}.nii.gz"
        if not path.exists():
            print(f"missing {path}")
            continue
        arr = sitk.GetArrayFromImage(sitk.ReadImage(str(path)))
        planes = [int(float(p)) for p in group.split(",")]
        tiles = []
        for zi in planes:
            # Axial index is the FIRST axis of the numpy view of a BraTS volume.
            plane = np.flipud(arr[zi])
            rgb = colourise(plane)
            Image.fromarray(rgb).save(outdir / f"c{case}_{args.suffix}_z{zi}.png")
            tiles.append(rgb)
        if tiles:
            sheet = np.concatenate(tiles, axis=1)
            Image.fromarray(sheet).save(outdir / f"c{case}_{args.suffix}_sheet.png")
            print(f"c{case}: {planes} -> {sheet.shape}")


if __name__ == "__main__":
    main()
