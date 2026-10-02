#!/usr/bin/env python3
"""Write the paired programs the differential kit compares.

WHY THESE ARE GENERATED

VoxLogicA 1 has no `for` and no directory listing, so a case over twenty
patients is twenty near-identical blocks of text. Writing those by hand is how
the two dialects drift apart, and a drift between the PROGRAMS is reported by
the kit as a disagreement between the ENGINES -- the one failure mode the
README warns about. So both dialects come out of one description here: the
operators are written once, and each dialect is a rendering of the same list.

WHAT IS COMPARED, AND WHY IT IS SAFE TO COMPARE IT

Only operators that both standard libraries define identically. VoxLogicA 1's
`src/stdlib.imgql` and VoxLogicA 2's `primitives/vox1/compat.imgql` agree line
for line on the derived operators used below; anything present in one and not
the other (`erode`, `dilate`, `imopen`, `imclose`) is defined inside the
program, in both dialects, so the comparison stays between evaluators.

Two differences are unavoidable and are handled rather than hidden: VoxLogicA 1
takes `border` as an atom while VoxLogicA 2 takes the image it belongs to, and
VoxLogicA 1 loads a file with `load` while VoxLogicA 2 reads it with
`ReadImage`. Everything else is the same text on both sides.
"""

from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE / "programs"
DATASET = "/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData"

#: The published recipe's constants, kept exactly as `tacas19` had them so the
#: twenty-case run is comparable with the three-case run it replaces.
HI_THR, VI_THR, CORRECTION = 0.95, 0.86, 0.5

#: One goal: a name, the VoxLogicA 1 expression, the VoxLogicA 2 expression.
#: Where the two are the same string the operator is shared between the
#: libraries, which is most of them; the list is the point of the `primitives`
#: case, so each line is one operator under test.
PRIMITIVE_GOALS: list[tuple[str, str, str]] = [
    ("vol_brain",      "volume(brain)",                  "volume(brain)"),
    ("vol_hi",         "volume(hi)",                     "volume(hi)"),
    ("vol_vi",         "volume(vi)",                     "volume(vi)"),
    ("vol_not",        "volume(!hi)",                    "volume(not(hi))"),
    ("vol_and",        "volume(hi & vi)",                "volume(and(hi, vi))"),
    ("vol_or",         "volume(hi | vi)",                "volume(or(hi, vi))"),
    ("vol_setminus",   "volume(vi \\ hi)",               "volume(vi \\ hi)"),
    ("vol_xor",        "volume(xor(hi, vi))",            "volume(xor(hi, vi))"),
    ("vol_near",       "volume(N(hi))",                  "volume(N(hi))"),
    ("vol_interior",   "volume(I(hi))",                  "volume(I(hi))"),
    ("vol_boundary",   "volume(boundaryL(hi))",          "volume(boundaryL(hi))"),
    ("vol_touch",      "volume(touch(vi, hi))",          "volume(touch(vi, hi))"),
    ("vol_grow",       "volume(grow(hi, vi))",           "volume(grow(hi, vi))"),
    ("vol_surrounded", "volume(surrounded(hi, brain))",  "volume(surrounded(hi, brain))"),
    ("vol_mayreach",   "volume(mayReach(hi, vi))",       "volume(mayReach(hi, vi))"),
    ("vol_distleq",    "volume(distleq(2.0, hi))",       "volume(distleq(2.0, hi))"),
    ("vol_distgeq",    "volume(distgeq(2.0, hi))",       "volume(distgeq(2.0, hi))"),
    ("vol_smoothen",   "volume(smoothen(hi, 2.0))",      "volume(smoothen(hi, 2.0))"),
    ("vol_erode",      "volume(erodeL(hi, 2.0))",        "volume(erodeL(hi, 2.0))"),
    ("vol_dilate",     "volume(dilateL(hi, 2.0))",       "volume(dilateL(hi, 2.0))"),
    ("vol_imopen",     "volume(imopenL(hi, 2.0))",       "volume(imopenL(hi, 2.0))"),
    ("vol_imclose",    "volume(imcloseL(hi, 2.0))",      "volume(imcloseL(hi, 2.0))"),
    ("max_flair",      "max(flair)",                     "max(flair)"),
    ("min_flair",      "min(flair)",                     "min(flair)"),
    ("max_pflair",     "max(pflair)",                    "max(pflair)"),
    ("dice_hi",        "dice(hi, gt)",                   "dice(hi, gt)"),
    ("dice_grow",      "dice(grow(hi, vi), gt)",         "dice(grow(hi, vi), gt)"),
]

#: Defined in the program rather than taken from a library, because VoxLogicA 1
#: has no morphology operators and VoxLogicA 2 has four -- and because `B+`, the
#: boundary, cannot be called in EITHER library: both define it before they
#: define the set difference it uses, so both reject it with "Unknown identifier
#: \\". The same bug on both sides, which is what a faithful transliteration
#: gets you. The operators themselves are fine, so the program defines it. Same definitions on
#: both sides, so what is compared is the evaluation and not the library.
LOCAL_VL1 = """let dice(x,y) = (2 .*. volume(x & y)) ./. (volume(x) .+. volume(y))
let erodeL(a,x) = !(distleq(x,!a))
let dilateL(a,x) = distleq(x,a)
let imopenL(a,x) = dilateL(erodeL(a,x),x)
let imcloseL(a,x) = erodeL(dilateL(a,x),x)
let boundaryL(a) = (N(a)) \\ a"""

LOCAL_VL2 = """dice(x, y) = (2 * volume(and(x, y))) / (volume(x) + volume(y))
erodeL(a, x) = not(distleq(x, not(a)))
dilateL(a, x) = distleq(x, a)
imopenL(a, x) = dilateL(erodeL(a, x), x)
imcloseL(a, x) = erodeL(dilateL(a, x), x)
boundaryL(a) = N(a) \\ a"""


def case_paths(n: int) -> tuple[str, str]:
    name = f"BraTS20_Training_{n:03d}"
    return (f"{DATASET}/{name}/{name}_flair.nii.gz",
            f"{DATASET}/{name}/{name}_seg.nii.gz")


def primitives_vl1() -> str:
    flair, seg = case_paths(1)
    goals = "\n".join(f'print "{name}" {expr}' for name, expr, _ in PRIMITIVE_GOALS)
    return f"""import "stdlib.imgql"

{LOCAL_VL1}

load imgFLAIR = "{flair}"
let flair = intensity(imgFLAIR)
load imgSeg = "{seg}"
let gt = intensity(imgSeg) >. 0

let background = touch(flair <. 0.1, border)
let brain = !background
let pflair = percentiles(flair, brain, {CORRECTION})
let hi = pflair >. {HI_THR}
let vi = pflair >. {VI_THR}

{goals}
"""


def primitives_vl2() -> str:
    flair, seg = case_paths(1)
    goals = "\n".join(f'print "{name}" {expr}' for name, _, expr in PRIMITIVE_GOALS)
    return f"""// Generated by generate_programs.py -- edit that, not this.
import "simpleitk"
import "vox1"

{LOCAL_VL2}

flair = intensity(ReadImage("{flair}"))
gt = geq_sv(1, intensity(ReadImage("{seg}")))

brain = not(touch(leq_sv(0.1, flair), border(flair)))
pflair = percentiles(flair, brain, {CORRECTION})
hi = geq_sv({HI_THR}, pflair)
vi = geq_sv({VI_THR}, pflair)

{goals}
"""


def tacas19_vl1(cases: int) -> str:
    blocks = []
    for n in range(1, cases + 1):
        flair, seg = case_paths(n)
        blocks.append(f"""// === case {n} ===
load imgFLAIR{n} = "{flair}"
let flair{n} = intensity(imgFLAIR{n})
load imgSeg{n} = "{seg}"
let gt{n} = intensity(imgSeg{n}) >. 0
let background{n} = touch(flair{n} <. 0.1, border)
let pflair{n} = percentiles(flair{n}, !background{n}, {CORRECTION})
let seg{n} = grow2(flt(5.0, pflair{n} >. {HI_THR}), flt(2.0, pflair{n} >. {VI_THR}))
print "dice_c{n}" dice(seg{n}, gt{n})""")
    return """import "stdlib.imgql"

let dice(x,y) = (2 .*. volume(x & y)) ./. (volume(x) .+. volume(y))
let distlt(x,y) = dt(y) <. x
let grow2(a,b) = (a|touch(b,a))
let flt(r,a) = distlt(r,distgeq(r,!a))

""" + "\n\n".join(blocks) + "\n"


def tacas19_vl2(cases: int) -> str:
    goals = "\n".join(f'print "dice_c{n}" dice(segment({n - 1}), gt_of({n - 1}))'
                      for n in range(1, cases + 1))
    return f"""// Generated by generate_programs.py -- edit that, not this.
//
// VoxLogicA 1's own constants and VoxLogicA 1's own `flt`, so the comparison is
// between engines rather than between two different smoothings: `smoothen` here
// instead cost 0.02 to 0.03 Dice per case and looked, for a while, like an
// engine divergence.
import "simpleitk"
import "vox1"

dataset_root = "{DATASET}"

flair_paths = subsequence(dir(dataset_root, "*_flair.nii.gz", true, true), 0, {cases})
gt_paths    = subsequence(dir(dataset_root, "*_seg.nii.gz",   true, true), 0, {cases})

distlt(x, y) = dt(y) <. x
flt(r, a) = distlt(r, distgeq(r, not(a)))
dice(x, y) = (2 * volume(and(x, y))) / (volume(x) + volume(y))

flair_of(g) = intensity(ReadImage(index(flair_paths, g)))
gt_of(g)    = geq_sv(1, intensity(ReadImage(index(gt_paths, g))))

pflair_of(g) =
  let flair = flair_of(g) in
  percentiles(flair, not(touch(leq_sv(0.1, flair), border(flair))), {CORRECTION})

segment(g) =
  let pflair = pflair_of(g) in
  grow(flt(5.0, geq_sv({HI_THR}, pflair)), flt(2.0, geq_sv({VI_THR}, pflair)))

{goals}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=20,
                        help="patients in the tacas19 case (default 20)")
    args = parser.parse_args()

    written = {
        "primitives.vl1.imgql": primitives_vl1(),
        "primitives.vl2.imgql": primitives_vl2(),
        f"tacas19x{args.cases}.vl1.imgql": tacas19_vl1(args.cases),
        f"tacas19x{args.cases}.vl2.imgql": tacas19_vl2(args.cases),
    }
    for name, text in written.items():
        (PROGRAMS / name).write_text(text, encoding="utf-8")
        print(f"wrote {name} ({len(text.splitlines())} lines)")
    print(f"{len(PRIMITIVE_GOALS)} primitive goals, {args.cases} patients")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
