"""Arm D of the A/B/C/D comparison in manuscripts/engine-scaling-2026-07.md
Part V: the reduced TACAS'19 recipe (threshold + grow, no cross-correlation),
run with NO VoxLogicA engine at all -- no DAG, no scheduler, no fusion, no
caching, no content-addressing. A straight-line Python loop over N cases
calling SimpleITK directly, sequentially.

This exists to answer a question the other three arms cannot: how much of
VL2's wall-clock is the engine's OWN machinery earning its keep, versus how
much any competent direct implementation would already get for free just by
calling ITK filters in an obvious order? Arms A-C (VL1, `main`, `incoming`)
all go through SOME orchestration layer; this one goes through none.

Two VL2 kernels are IMPORTED rather than reimplemented:
  - `percentiles` (voxlogica.primitives.vox1.kernels) -- a custom
    parallel-sort rank-normalization, not a single ITK filter call. See that
    module's docstring for the algorithm.
  - `through` (same module) -- composes ConnectedComponent with a
    label-intersection step; also not a single filter call.
Reimplementing either by hand here would risk a subtle behavioral
discrepancy for no purpose: the question under test is orchestration
overhead, not algorithm-reimplementation fidelity. `border` is imported for
the same reason (a real, if simple, custom voxel-marking routine, not an ITK
filter). Every other operation below (thresholds, boolean ops, distance
transform, dilation, mask) is exactly one direct SimpleITK call, written
inline -- these ARE "pure ITK" and importing them would add nothing but an
extra layer of indirection.

Operator semantics (pdt/distgeq/distleq/smoothen) were confirmed against the
REAL VL1 binary's stdlib.imgql operators (`.<=`/`.>=`) on a synthetic test
array before being trusted here, not derived from the formulas alone --
see manuscripts/engine-scaling-2026-07.md Part V for that verification.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import SimpleITK as sitk


def _add_vl2_kernels_to_path(repo_root: Path) -> None:
    sys.path.insert(0, str(repo_root / "implementation" / "python"))


def _import_vl2_kernels():
    from voxlogica.primitives.vox1.kernels import border as vl2_border
    from voxlogica.primitives.vox1.kernels import percentiles as vl2_percentiles
    from voxlogica.primitives.vox1.kernels import through as vl2_through
    return vl2_border, vl2_percentiles, vl2_through


# ---- "pure ITK" building blocks: one filter call each ---------------------

def near(image: sitk.Image) -> sitk.Image:
    """Dilate by one voxel, 26-connectivity box kernel."""
    return sitk.BinaryDilate(sitk.Cast(image, sitk.sitkUInt8), [1, 1, 1], sitk.sitkBox, 1.0)


def geq_sv(value: float, image: sitk.Image) -> sitk.Image:
    return sitk.GreaterEqual(image, float(value))


def leq_sv(value: float, image: sitk.Image) -> sitk.Image:
    return sitk.LessEqual(image, float(value))


def logical_not(image: sitk.Image) -> sitk.Image:
    return sitk.Not(sitk.Cast(image, sitk.sitkUInt8))


def logical_and(a: sitk.Image, b: sitk.Image) -> sitk.Image:
    return sitk.And(sitk.Cast(a, sitk.sitkUInt8), sitk.Cast(b, sitk.sitkUInt8))


def logical_or(a: sitk.Image, b: sitk.Image) -> sitk.Image:
    return sitk.Or(sitk.Cast(a, sitk.sitkUInt8), sitk.Cast(b, sitk.sitkUInt8))


def dt(image: sitk.Image) -> sitk.Image:
    flt = sitk.SignedMaurerDistanceMapImageFilter()
    flt.SetInsideIsPositive(False)
    flt.SetSquaredDistance(False)
    flt.SetUseImageSpacing(True)
    flt.SetBackgroundValue(0.0)
    return flt.Execute(sitk.Cast(image, sitk.sitkUInt8))


def volume(image: sitk.Image) -> float:
    return float(np.count_nonzero(sitk.GetArrayViewFromImage(sitk.Cast(image, sitk.sitkUInt8))))


# ---- composed operations, matching compat.imgql's formulas exactly --------
# pdt(x)      = mask(dt(x), dt(x) > 0)
# distgeq(x,y)= x .<= pdt(y)   =  pdt(y) >= x   (confirmed empirically: see
#                                 module docstring -- ".<=" is scalar-first,
#                                 "s .<= img" means "img's value >= s")
# distleq(x,y)= x .>= pdt(y)   =  pdt(y) <= x
# smoothen(a,x) = distleq(x, distgeq(x, not(a)))
# touch(a,b)  = through(near(b), a)
# grow(a,b)   = a | touch(b,a)
#
# dt(x) is computed ONCE per pdt() call and reused for both the mask image
# and the threshold condition -- exactly what a competent Python programmer
# would do with a local variable, and exactly what VL2's content-addressing
# gives arms B/C for free. Testing orchestration overhead means giving this
# arm the same obvious optimization, not testing whether someone forgot to
# reuse a variable.

def pdt(x: sitk.Image) -> sitk.Image:
    d = dt(x)
    return sitk.Mask(d, sitk.Cast(sitk.Greater(d, 0.0), sitk.sitkUInt8), 0.0)


def distgeq(radius: float, y: sitk.Image) -> sitk.Image:
    return geq_sv(radius, pdt(y))


def distleq(radius: float, y: sitk.Image) -> sitk.Image:
    return leq_sv(radius, pdt(y))


def smoothen(a: sitk.Image, radius: float) -> sitk.Image:
    return distleq(radius, distgeq(radius, logical_not(a)))


def touch(a: sitk.Image, b: sitk.Image, through_fn) -> sitk.Image:
    return through_fn(near(b), a)


def grow(a: sitk.Image, b: sitk.Image, through_fn) -> sitk.Image:
    return logical_or(a, touch(b, a, through_fn))


# ---- the recipe (matches looping_experiment/test_speedup.py's TEMPLATE) ---

HI_THR = 0.93
VI_THR = 0.88


def run_case(flair_path: str, seg_path: str, border_fn, percentiles_fn, through_fn) -> float:
    flair = sitk.ReadImage(flair_path)
    flair = sitk.Cast(flair, sitk.sitkFloat32)

    background = touch(leq_sv(0.1, flair), border_fn(flair), through_fn)
    brain = logical_not(background)
    pflair = percentiles_fn(flair, brain, 0)

    hyper = smoothen(geq_sv(HI_THR, pflair), 5.0)
    very = smoothen(geq_sv(VI_THR, pflair), 2.0)
    pred = grow(hyper, very, through_fn)

    seg = sitk.ReadImage(seg_path)
    gt = geq_sv(1.0, sitk.Cast(seg, sitk.sitkFloat32))

    inter = volume(logical_and(pred, gt))
    return (2.0 * inter) / (volume(pred) + volume(gt))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-root", required=True,
                         help="directory of BraTS20_Training_NNN/ case folders")
    parser.add_argument("--cases", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repo-root", default=None,
                         help="VoxLogicA-2 repo root, to import the two VL2 kernels from "
                              "(default: derived from this script's own location, which "
                              "only works if it hasn't been copied elsewhere)")
    args = parser.parse_args()

    if args.repo_root is None:
        # Lazy, not an eager argparse default: this script is sometimes run
        # from a copy outside the real repo tree for a quick smoke test, where
        # parents[3] doesn't exist -- fail only if it's actually needed.
        args.repo_root = str(Path(__file__).resolve().parents[3])

    _add_vl2_kernels_to_path(Path(args.repo_root))
    border_fn, percentiles_fn, through_fn = _import_vl2_kernels()

    def case_paths(i: int) -> tuple[str, str]:
        name = f"BraTS20_Training_{i:03d}"
        base = Path(args.dataset_root) / name
        return str(base / f"{name}_flair.nii.gz"), str(base / f"{name}_seg.nii.gz")

    if args.warmup:
        for i in range(1, args.warmup + 1):
            run_case(*case_paths(i), border_fn, percentiles_fn, through_fn)

    dice_values = []
    t0 = time.perf_counter()
    for i in range(1, args.cases + 1):
        dice_values.append(run_case(*case_paths(i), border_fn, percentiles_fn, through_fn))
    wall = time.perf_counter() - t0

    for i, d in enumerate(dice_values, start=1):
        print(f"dice_c{i}={d:.10f}")
    print(f"wall={wall:.2f}s  per_case={wall/args.cases:.3f}s  cases={args.cases}")


if __name__ == "__main__":
    main()
