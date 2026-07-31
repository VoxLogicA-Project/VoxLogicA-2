"""Unroll N per-case VL1 blocks into ONE .imgql file for a fair VL1 timing.

Why this exists: VL1 (the real historical VoxLogicA binary --
/home/VoxLogicA/binaries/VoxLogicA_1.3.3-experimental_linux-x64 on fmt-5000,
not part of this repo) has no `for`-loop or `dir()` construct, so a naive
multi-case benchmark drives it as one process launch per case. That is WRONG
for a timing comparison against VL2 (which amortizes one process across all
cases via its native loop): it charges VL1 for one dotnet startup per case,
which is pure overhead unrelated to the computation. Measured cost of that
mistake, 40 cases: 41.76s (wrong, 40 processes) vs 8.16s (this script, 1
process) -- 34 of those 41.76 seconds were overhead, not compute. See
manuscripts/engine-scaling-2026-07.md Part III sec 14 for the full account,
including how this was caught (not by internal review -- by a second,
structurally different measurement disagreeing with the first).

VL1 does not need a loop construct to avoid the overhead: N independent
`load`/`let`/`print` blocks can simply be concatenated into one file, exactly
the way VL1's own real multi-block scripts already do for PARAMETER sweeps
(/home/VoxLogicA/scripts/gen_GBM_multi.sh, sweeping thresholds this way, not
cases) -- this script does the same thing, sweeping cases instead.

GOTCHA, found the hard way: VL1 identifiers cannot contain an underscore at
all (confirmed empirically: `load img_1 = ...` fails to parse with "Expecting:
whitespace or '='" right at the "_"; `load img1 = ...` parses fine). So the
per-case suffix below is letters+digits only ("flairc12"), never "flair_12" --
underscores are fine inside STRING literals (print labels), just not in bare
identifiers.

A second gotcha this script guards against with word-boundary regex, not
naive str.replace: "flair" is a substring of "pflair", and of the literal
"..._flair.nii.gz" filename text. A first attempt using str.replace corrupted
both -- "pflair" got double-suffixed, and the file path was silently mangled
into a nonexistent filename. `\\bname\\b` matches only the whole-word
identifier, not the substring inside a longer word or a file path.
"""

from __future__ import annotations

import argparse
import re


def build(template_path: str, out_path: str, n_cases: int, dataset_root: str,
          case_name_fmt: str = "BraTS20_Training_{:03d}") -> None:
    tpl = open(template_path).read()
    lines = tpl.splitlines()
    prelude_end = next(i for i, l in enumerate(lines) if l.startswith("load "))
    prelude = "\n".join(lines[:prelude_end])
    per_case_tpl = "\n".join(lines[prelude_end:])

    # Every identifier the per-case template binds via `let`/`load` -- must be
    # kept in sync with reduced_recipe.imgql.template if that file changes.
    # "dice"/"grow2"/"flt"/"distlt" are the SHARED prelude functions and must
    # stay untouched (they are not in this list).
    names_to_suffix = [
        "imgFLAIR", "flair", "pflair", "imgManualSeg", "manualContouringGTV",
        "diceM", "background", "brain", "hI", "vI", "hyperIntense",
        "veryIntense", "growTum",
    ]

    out = [prelude, ""]
    for i in range(1, n_cases + 1):
        case_name = case_name_fmt.format(i)
        block = per_case_tpl.replace("$INPUTDIR", f"{dataset_root}/{case_name}")
        block = block.replace("$NAME", case_name)
        for name in names_to_suffix:
            block = re.sub(rf"\b{name}\b", f"{name}c{i}", block)
        block = block.replace('print "dice"', f'print "dice_c{i}"')
        out.append(f"// === case {i}: {case_name} ===")
        out.append(block)
        out.append("")

    text = "\n".join(out)
    with open(out_path, "w") as f:
        f.write(text)

    # Cheap sanity checks -- catch the two gotchas above before a 40-case run
    # burns real time discovering them the hard way.
    assert "load imgFLAIRc1 " in text, "case 1 load line missing/malformed"
    assert "flairc1.nii.gz" not in text, "path corruption: rename leaked into a file path"
    assert "pflairc1c1" not in text, "double-suffix bug reintroduced"
    for i in range(1, n_cases + 1):
        assert f'print "dice_c{i}"' in text, f"missing print for case {i}"
    print(f"sanity checks passed; {n_cases} cases -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", default="reduced_recipe.imgql.template")
    parser.add_argument("--out", default="/tmp/vl1_onefile.imgql")
    parser.add_argument("--cases", type=int, default=40)
    parser.add_argument("--dataset-root",
                         default="/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData",
                         help="VL1-host-local path to the BraTS2020 training set "
                              "(not part of this repo -- fmt-5000 only)")
    args = parser.parse_args()
    build(args.template, args.out, args.cases, args.dataset_root)


if __name__ == "__main__":
    main()
