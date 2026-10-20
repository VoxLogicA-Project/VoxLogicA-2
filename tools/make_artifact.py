#!/usr/bin/env python3
"""Build the TACAS artifact directory from this checkout, repeatably.

Run it as often as you like: the engine is still moving, so the artifact is a
BUILD PRODUCT, not a directory anyone edits by hand. Nothing here is copied out
of a previous artifact -- every run starts from the working tree, so what you
ship is what the repository says today.

    python3 tools/make_artifact.py                 # code + programs
    python3 tools/make_artifact.py --with-model    # ... and the trained weights
    python3 tools/make_artifact.py --tar           # ... and a tarball beside it

Stdlib only, and plain `python3`: this must work before `bootstrap.py` has
built anything.

WHAT IMAGE DATA LEAVES. The twenty-five BraTS 2020 cases the shipped programs
read, and nothing else: the twenty both sweeps score and the five the sampler
reads, copied from the official training set. The slice comes out of the
programs' own literals, so this cannot ship a window they do not read. nnU-Net's
`nnUNet_raw`/`nnUNet_preprocessed` trees do not travel -- they are the same
volumes rewritten, and the shipped program does not need them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Where this artifact is archived. Reserved on Zenodo before the tag, so the
#: README can name it and the archived copy can name the README.
DOI = "10.5281/zenodo.23340242"

#: Marks a directory as ours to overwrite. Without it the script will not
#: delete anything: pointing --out at a home directory should not be fatal.
MARKER = ".voxlogica-artifact"

# --------------------------------------------------------------- manifest ---
# Edit these five lists as the tree moves. Everything else follows from them.

#: What makes the tool run. Paths are repo-relative and keep their layout in
#: the artifact, so a program's relative imports and paths still resolve.
ENGINE: list[str] = [
    "LICENSE",                                # GPL-3.0; the AEC needs one to evaluate at all
    "voxlogica",                              # the launcher
    "bootstrap.py",                           # builds .venv, fetches uv
    ".python-version",                        # 3.14t -- free-threaded, required
    "implementation/python/voxlogica",        # the engine itself
    "implementation/python/requirements.txt",
    # The locks, not just the requirements: requirements.txt says what we depend
    # on, the lock says what THOSE depend on, with hashes. Only the second makes
    # a rebuild months from now resolve the stack these numbers came from.
    "implementation/python/requirements.lock",
    # No requirements-test.lock: tests/ does not ship, so the reviewer's venv
    # is the runtime only. bootstrap.py installs from whichever locks exist.
    # The dataset contract, which still has a job now that the cases ship: it
    # pins them to the OFFICIAL set. `--subset` checks the twenty-five here
    # against the manifest of all 369, so a reviewer can see that what we
    # shipped is BraTS unmodified rather than something we prepared.
    "tools/dataset_manifest.py",
    "tools/brats2020-manifest.json",
    # Not an experiment: the one-second program the kick-the-tires phase runs to
    # see the engine COMPUTE something, rather than only parse and typecheck.
    "doc/gallery/programs/default/default-range-map.imgql",
]

#: The measurement suite. The paper's performance figures were taken with this
#: code and not with a stopwatch, so it ships: a reviewer who doubts a number
#: can retake it instead of believing it. It measures from OUTSIDE the engine
#: -- wall clock, processor time and peak RSS come from `os.wait4` -- so an
#: error in the engine's own bookkeeping cannot flatter its own measurement.
#:
#: Pure standard library, deliberately: it runs under the reviewer's system
#: `python3` and MEASURES the venv that `bootstrap.py` builds, which keeps the
#: measuring apparatus out of the measured stack. `vl1_compare.py` imports
#: `suite`, so the two travel together or neither works.
MEASUREMENT: list[str] = [
    "tools/perf/suite.py",       # the harness: experiments, repetitions, gating
    "tools/perf/report.py",      # the summary table, and what makes a row untrustworthy
    "tools/perf/vl1_compare.py", # VoxLogicA 1 against VoxLogicA 2, same measurements
]

#: The experiments, in the order the README presents them. `needs_dataset`
#: drives what the README says each one reads.
PROGRAMS: list[dict] = [
    {
        "path": "doc/gallery/programs/nnunet/nnunet-circle-segmentation.imgql",
        "title": "Installation check, synthetic data",
        "needs_dataset": False,
        "needs_gpu": False,
        # It trains too, so its numbers are no more reproducible than the sweep's.
        # But it has no numbers: it is a pass/fail check that the install works.
        "reproducibility": "pass/fail only",
        "minutes": "51 min on 24 CPU cores, 77 min on 4 (both measured)",
        "note": "Synthetic circles and squares. No data of any kind: the images are "
                "generated by the program. It trains a small nnU-Net on the CPU and "
                "predicts with it, so a green run proves the whole install -- engine, "
                "SimpleITK, torch, nnU-Net. This belongs to the full evaluation, not to "
                "kick-the-tires: it takes about an hour. Start it in the background and "
                "read on.",
        "outputs": "output/nnunet-circle-segmentation/",
        "expect": [
            "10 files under `training/` -- `case_0..4_image.png` and `case_0..4_label.png`",
            "2 files under `predictions/` -- `test_a_input.png`, `test_b_input.png`",
            "2 more under `predictions/` -- `test_a_segmentation.png`, "
            "`test_b_segmentation.png`, each a white blob where the network found "
            "the circle",
        ],
        "total_files": 14,
    },
    {
        "path": "doc/gallery/programs/brats2020/brats-five-cases.imgql",
        "title": "TACAS recipe, five BraTS2020 cases",
        "reproducibility": "bit-identical",
        "needs_dataset": True,
        "minutes": "under a minute (6 s on 24 cores, 31 s on 4)",
        "note": "The published whole-tumour recipe on FLAIR alone, scored per case "
                "against the ground truth. Five cases, so it answers 'is the method "
                "reproducing' in minutes rather than in an hour.",
        "outputs": "case_230_segmentation.nii.gz (beside the program)",
        "expect": [
            "one `case_*_best` goal per case, each a Dice score",
            "`average_best`, the mean of those five",
            "`case_230_sweep`, one Dice per candidate threshold",
        ],
        "total_files": 1,
    },
    {
        "path": "doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql",
        "title": "AIIM threshold sweep",
        "reproducibility": "bit-identical",
        "needs_dataset": True,
        "minutes": "30 s - 2 min (29 s on 24 cores, 65 s single-threaded)",
        "note": "The AIIM study as one declarative program: sweep the permissive "
                "threshold over 0.72-0.92, keep the best per case, and report the "
                "spread of the winning thresholds. This is the paper's experiment. "
                "Scored on HGG cases 051-070 -- the same twenty the nnU-Net program "
                "evaluates, so the symbolic and the hybrid figure are comparable, and "
                "outside that program's training set so neither method is in-sample.",
        "outputs": "output/aiim-sweep/",
        "expect": [
            "`dice_fixed_mean`, `dice_fixed_median`, `dice_fixed_stdev` -- the single "
            "published threshold, scored",
            "`dice_best_mean`, `dice_best_median`, `dice_best_stdev` -- the per-case best",
            "`fixed_dice` and `best_dice` -- the twenty per-case scores behind each "
            "of those two families, so a reader can recompute the statistics",
            "`vi_thr_distribution` -- which thresholds won, and how often. A SPREAD "
            "here rather than one winner is the study's result",
            "`case_start` -- 50, a guard: it says which twenty cases were scored",
            "90 PNGs under `output/aiim-sweep/`, nine for each of the ten worst "
            "cases: three planes, and for each plane the FLAIR, the segmentation "
            "and the ground truth",
            "those PNGs are 90 DIFFERENT images -- grey anatomy, with a white mask "
            "on the segmentation and ground-truth ones. All black, or nine "
            "identical files per case, means the `Extract` index is being dropped: "
            "the engine that ships here fixed that, an older one did not",
        ],
        "total_files": 90,
    },
    {
        "path": "doc/gallery/programs/nnunet/brats-threshold-sweep-nnunet.imgql",
        "title": "AIIM sweep against a learned reference",
        "needs_dataset": True,
        "needs_gpu": True,
        "reproducibility": "to 1e-4",
        "minutes": "about 80 s at 4 threads (77 s measured)",
        "note": "The same sweep as experiment 3, scored against nnU-Net's prediction "
                "instead of the radiologist's annotation. It asks whether the "
                "case-dependence of the permissive threshold survives when the "
                "reference stops being a human annotation. It does not train: "
                "`nnunet.load_model` names the weights that ship in `model/`.",
        "nondeterminism": (
            "**Reproducible to 1e-4, not bit-identical.** The network is fixed -- these "
            "are the shipped weights, not a retraining -- but nnU-Net's sliding-window "
            "inference on a GPU is not bitwise deterministic. Across six runs the "
            "per-case Dice values moved by at most 1.9e-05 and every aggregate in the "
            "sixth decimal, while the winning thresholds came back identical every "
            "time. Compare `vi_thr_distribution` and `best_vi_thr` exactly; compare "
            "Dice to 1e-4."),
        "run_flags": "--threads 4",
        "flags_note": (
            "**Run this one with `--threads 4`**, as the command below does: it is the "
            "fastest configuration we measured and the one these figures were taken at. "
            "It peaks at 7.6 GB, so 16 GB of RAM is enough. The 34 goals do not depend "
            "on the width."),
        "outputs": "output/aiim-sweep-nnunet/",
        "expect": [
            "`model_dice_mean` -- how close the learned reference is to the "
            "annotation it replaced. Read this FIRST: it bounds everything below, "
            "because a threshold agreeing with a bad model means nothing",
            "`vi_thr_distribution` -- the same question as experiment 3, asked of "
            "the learned reference",
            "`best_dice`, `best_vi_thr` -- per case",
            "`dice_fixed_mean`, `dice_fixed_median`, `dice_fixed_stdev` and the "
            "per-case `fixed_dice` -- the single published threshold, scored",
            "120 PNGs under `output/aiim-sweep-nnunet/`, twelve for each of the ten "
            "worst cases: three planes, and for each plane FOUR images -- `_flair`, "
            "`_predicted` (the sweep), `_nnunet` (the learned reference it was scored "
            "against) and `_truth` (the annotation, for the reader's own judgement)",
            "those PNGs are 120 DIFFERENT images -- grey anatomy with a white mask on "
            "the three masked ones. All black means the `Extract` index is being "
            "dropped; the engine that ships here fixed that",
        ],
        "total_files": 120,
    },
]

#: Trained weights, so a reviewer need not repeat the training. `keep` is an
#: allowlist BY FILENAME: everything else under the source tree stays behind,
#: which is what keeps BraTS-derived volumes out.
#:
#: WHAT THESE DO AND DO NOT SAVE. `nnUNetv2_plan_and_preprocess` runs
#: unconditionally in runtime.train_model, BEFORE the per-fold "checkpoint
#: already exists" check, so shipped weights save the TRAINING (the GPU hours),
#: not the dataset: the raw cases are still materialized and preprocessed. A
#: reviewer without BraTS cannot run the nnU-Net program at all.
#: Where the weights are on the BUILD machine -- not shipped, not in the
#: artifact, and overridable with --model-source. It has to be the `work_root`
#: the nnU-Net program was actually trained against, so the weights that get
#: picked up are the ones that program produced: pointing it elsewhere is how
#: you ship someone else's model by accident, and the check in step 6 is what
#: catches it when you do.
MODEL_SOURCE = Path.home() / "nnunet-brats-work"

#: THE IMAGE DATA. The artifact ships the BraTS 2020 cases the programs read --
#: the twenty both sweeps score and the five the sampler reads -- and not the
#: other 344, which no shipped program names. They are copied from a reference
#: copy of the official training set on the build machine (--dataset-source),
#: and the slice is read out of the programs themselves rather than written
#: here, so a program that moves its window ships the cases it moved to.
DATASET_SOURCE = Path("/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData")

#: Where they land, and what the shipped programs are rewritten to name.
#: Relative: the README has the reviewer run from the artifact root, so nothing
#: in the artifact names a path that only exists on one machine.
DATASET_DEST = "data/MICCAI_BraTS2020_TrainingData"
SAMPLE_DEST = "doc/gallery/programs/brats2020/data"

MODEL = {
    "source": MODEL_SOURCE / "nnUNet_results",
    "manifest": MODEL_SOURCE / "voxlogica_manifest.json",
    "dest": "model/nnUNet_results",
    # postprocessing.pkl is THE DECISION, not data: two operation names and the
    # label they apply to, 260 bytes, sitting under fold_0/validation/ beside
    # the validation predictions that are not shipped. Without it the reviewer's
    # run finds nothing to determine postprocessing from and predicts RAW --
    # measured: every aggregate off by 1e-2 against the oracle, the threshold
    # distribution different.
    "keep": {"checkpoint_final.pth", "dataset.json", "dataset_fingerprint.json",
             "plans.json", "postprocessing.pkl"},
    #: Named so the refusal can be explained, not just performed.
    "refused": ["nnUNet_raw", "nnUNet_preprocessed"],
}

#: Weights are reused only when the dataset folder, trainer and configuration
#: all match what the program asks for -- nnU-Net looks them up by that path,
#: and runtime.train_model deliberately refuses another trainer's fold_0
#: ("reusing it would answer the program's question with someone else's
#: answer"). So shipping weights no program can name is shipping 236 MB of
#: nothing, and this is checked rather than hoped for.
#: The dataset folder is NOT among them: `nnunet.load_model` reads it from the
#: work root's own manifest, which ships beside the weights, so it cannot
#: disagree with them. What the program still chooses is the trainer and the
#: configuration, and those two have to name the fold that is here.
MODEL_SETTINGS_RE = {
    "trainer": re.compile(r'^\s*nnunet_trainer\s*=\s*"([^"]+)"', re.MULTILINE),
    "configuration": re.compile(r'^\s*nnunet_config\s*=\s*"([^"]+)"', re.MULTILINE),
}

#: What each experiment printed here, so a reviewer can tell "it ran" from
#: "it ran and was right". Kept as data rather than prose so it can be replaced
#: wholesale when we have more runs -- and so the README cannot describe an
#: oracle the manifest does not have.
#:
#: `tolerance` is 0 where repetition has shown the values do not move, and
#: non-zero only where it has shown they do. Do not soften a 0 to make a
#: comparison pass: the whole value of the number is that it is exact.
ORACLE: dict[str, dict] = {
    "doc/gallery/programs/brats2020/brats-five-cases.imgql": {
        "provenance": "Repeated runs, identical. These same values are in "
                      "doc/gallery/programs/brats2020/README.md, measured "
                      "independently and earlier.",
        "tolerance": 0.0,
        "values": [
            ("case_002_best", "0.8959418354477335", "compact, near-median tumour"),
            ("case_079_best", "0.0", "**expected**: smallest tumour in BraTS "
                                     "(7,285 voxels), raises no seed. See below."),
            ("case_089_best", "0.6518925047022751", "most multifocal case"),
            ("case_230_best", "0.9584736373313636", "the median case"),
            ("case_328_best", "0.9131447438872032", "largest tumour"),
            ("average_best", "0.6838905442737151", "mean of the five"),
        ],
        "note": "**A Dice of exactly 0.000 on case 079 is the expected result.** "
                "The method seeds on the top 5% of FLAIR intensity within "
                "the brain and grows from there; a tumour that small raises no seed, "
                "so there is nothing to grow. The case is in the sample to show where "
                "the method does not apply, which a sample of five favourable cases "
                "would hide. If you get 0.000 here, you "
                "have reproduced our result.",
    },
    "doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql": {
        "provenance": "Nine runs, bit-identical throughout. Seven on 2026-10-08: the shipped program on an empty store at 24 threads and at 1, plus an earlier formulation of the same case selection at 24 threads, at 4, warm, and under the lazy strategy, and one that added the fixed threshold's spread. Two ways of naming the same twenty cases agree digit for digit. Two more on 2026-10-12, across a SimpleITK wrapper fix that changed every exported image: the printed values did not move.",
        "tolerance": 0.0,
        "values": [
            ("case_start", "50.0",
             "which cases these are: HGG positions 50-69, i.e. 051-070. The same "
             "twenty the nnU-Net program evaluates, and outside its training set"),
            ("dice_fixed_mean", "0.8276376252018517", "the single published threshold"),
            ("dice_fixed_median", "0.8575208544440295", ""),
            ("dice_fixed_stdev", "0.12851916465807944", ""),
            ("dice_best_mean", "0.8554965584750516", "per-case best"),
            ("dice_best_median", "0.9073711220615291", ""),
            ("dice_best_stdev", "0.11245649918780284", ""),
            ("vi_thr_mean", "0.8895", ""),
            ("vi_thr_median", "0.895", ""),
            ("vi_thr_stdev", "0.02837252191822224", ""),
            ("vi_thr_distribution",
             "[[0.82,1],[0.85,2],[0.86,2],[0.88,1],[0.89,4],[0.9,4],[0.91,1],[0.92,5]]",
             "**the study's result**: eight thresholds win across twenty cases"),
        ],
        "note": "These are exact. The engine is deterministic in its values: the same "
                "digits came back at every thread count and with the disk cache both "
                "present and absent. A difference here is a real difference, not "
                "scheduling noise -- start with your SimpleITK version.",
    },
    "doc/gallery/programs/nnunet/brats-threshold-sweep-nnunet.imgql": {
        "provenance": "Six independent cold-store runs (2026-09-08 x2, 2026-09-11, "
                      "2026-09-15, 2026-10-12, 2026-10-20) on the same trained weights "
                      "and the same 20 cases; the 2026-09-15 one on this artifact as a "
                      "reviewer would run it -- fresh checkout, shipped weights, empty "
                      "store. The values are the 2026-10-12 run, the first to print "
                      "every goal. The 2026-10-20 one is the first to LOAD the weights "
                      "rather than restate the training that produced them, which is "
                      "how the shipped program names them; it sits within 1.9e-05 of "
                      "these values, and the winning thresholds are identical in all "
                      "six.",
        "tolerance": 1e-4,
        "values": [
            ("model_dice_mean", "0.8587657935069911",
             "how close the learned reference is to the annotation. Read first"),
            ("model_dice_median", "0.9152163651578276", ""),
            ("model_dice_stdev", "0.11011142897334406",
             "large: on some cases the network is weak"),
            ("model_sensitivity_mean", "0.821302951428539",
             "per case 0.38-0.98: where this is low, the reference is weak"),
            ("model_specificity_mean", "0.9994939912279985", "whole-volume; read differences"),
            ("dice_fixed_mean", "0.8069294373520195", ""),
            ("dice_fixed_median", "0.8846858248141457", ""),
            ("dice_fixed_stdev", "0.22969636865562157",
             "twice experiment 3's spread, and one case scores 0.000: the fixed "
             "threshold fails outright where the sweep does not"),
            ("sensitivity_fixed_mean", "0.9164727345951725", ""),
            ("specificity_fixed_mean", "0.9979484205242406", ""),
            ("dice_best_mean", "0.850164485800129", ""),
            ("dice_best_median", "0.9258509606139985", ""),
            ("dice_best_stdev", "0.18727226965618532", ""),
            ("sensitivity_best_mean", "0.9431586991502892",
             "the sweep's gain over fixed is almost all recall"),
            ("specificity_best_mean", "0.9973265001914303", "and costs almost nothing here"),
            ("vi_thr_mean", "0.8945000000000001", "**exact**, no tolerance"),
            ("vi_thr_median", "0.9", "**exact**"),
            ("vi_thr_stdev", "0.041986840043543486", "**exact**"),
            ("vi_thr_distribution", "[[0.73,1],[0.87,2],[0.89,4],[0.9,4],[0.91,2],[0.92,7]]",
             "**exact**, and the point of the experiment"),
        ],
        "note": "**What the stability means.** The winning thresholds came back identical "
                "in every run, across a retraining of the network and across loading it "
                "rather than restating its training. That is the claim this experiment "
                "makes: the case-dependence of the permissive threshold survives "
                "replacing a human annotation with a learned one. Report Dice, "
                "sensitivity and specificity to 3-4 decimals; past that is noise.\n\n"
                "**Specificity is over the whole volume**, as BraTS scores it, so it is "
                "dominated by the air around the head and sits near 1 for any plausible "
                "segmentation. The differences between rows carry the information; the "
                "absolute level does not.\n\n"
                "**The population is the 293 HGG cases**, selected by the grade in the "
                "dataset's own `name_mapping.csv`, not by directory order. The 20 evaluated "
                "are positions 50-69 of that list, which are BraTS20_Training_051 to _070.",
    },
}

#: Never copied, wherever they appear.
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

#: A program with no `print`/`save` computes nothing and exits 0 (the engine now
#: refuses it). Catching that here keeps a dud out of the artifact.
GOAL_RE = re.compile(r"^[ \t]*(print|save)[ \t]", re.MULTILINE)
PRINT_RE = re.compile(r"^[ \t]*print[ \t]", re.MULTILINE)
#: Absolute dataset paths are machine-specific; the README must list them.
DATASET_RE = re.compile(r"^\s*(\w*dataset_root|work_root)\s*=\s*\"([^\"]+)\"", re.MULTILINE)


# ------------------------------------------------------------------ steps ---
def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}", flush=True)


def _skip(path: Path) -> bool:
    return path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES


def copy_tree(src: Path, dst: Path) -> tuple[int, int]:
    """Copy a file or directory, dropping caches. Returns (files, bytes)."""
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=True)
        return 1, dst.stat().st_size
    files = total = 0
    for item in sorted(src.rglob("*")):
        if any(_skip(p) for p in (item, *item.parents)):
            continue
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target, follow_symlinks=True)
            files += 1
            total += target.stat().st_size
    return files, total


def hgg_ids(dataset_root: Path) -> list[str]:
    """The HGG cases in the order `name_mapping.csv` lists them -- the selection
    both sweeps make, reproduced so the build can ship exactly what they read."""
    rows = (dataset_root / "name_mapping.csv").read_text(encoding="utf-8").splitlines()
    return [r.split(",")[-1].strip() for r in rows if r.split(",")[0].strip() == "HGG"]


def scored_cases(dataset_root: Path) -> list[str]:
    """The cases the two sweeps score, from the programs' own literals.

    Both select by name from `name_mapping.csv` and then slice, so the shipped
    population is decided by two numbers in each program. Reading them here
    rather than restating them means a build cannot ship a window the programs
    do not read -- and it stops if the two programs stop agreeing, because the
    comparability of experiments 3 and 4 rests on their scoring the same cases.
    """
    ids = hgg_ids(dataset_root)
    chosen: dict[str, list[str]] = {}
    for rel, start_name, count_name in (
            ("doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql",
             "case_start", "case_count"),
            ("doc/gallery/programs/nnunet/brats-threshold-sweep-nnunet.imgql",
             "eval_start", "eval_count")):
        text = (REPO / rel).read_text(encoding="utf-8")
        window = []
        for name in (start_name, count_name):
            found = re.search(rf"^\s*{name}\s*=\s*(\d+)", text, re.MULTILINE)
            if not found:
                sys.exit(f"{rel}: no `{name}` literal; the build reads it to know "
                         f"which cases to ship")
            window.append(int(found.group(1)))
        start, count = window
        chosen[rel] = ids[start:start + count]
        if len(chosen[rel]) != count:
            sys.exit(f"{rel}: wants HGG positions {start}-{start + count - 1}, but "
                     f"{dataset_root.name} has {len(ids)} HGG cases")
    if len({tuple(v) for v in chosen.values()} ) != 1:
        sys.exit("the two sweeps no longer score the same cases, so shipping one set "
                 "would starve one of them:\n  "
                 + "\n  ".join(f"{k}: {v[0]}..{v[-1]}" for k, v in chosen.items()))
    return next(iter(chosen.values()))


def point_at_shipped_data(out: Path) -> list[tuple[str, str, str]]:
    """Rewrite the copied programs' roots to the shipped data, and say what was
    rewritten. The repository keeps placeholders -- a path that is right on one
    machine is wrong everywhere else -- and the artifact, which carries the data,
    does not need the reviewer to type anything."""
    rewritten = []
    for spec in PROGRAMS:
        path = out / spec["path"]
        text = original = path.read_text(encoding="utf-8")
        # Only the PLACEHOLDERS: the five-case sampler already names a path
        # inside the artifact, and rewriting that would point it at the sweeps'
        # twenty cases instead of its own five.
        for name, value in ((r"\w*dataset_root", DATASET_DEST), ("work_root", "model")):
            def swap(m, spec=spec, value=value):
                rewritten.append((spec["path"], m.group(2).strip(), value))
                return f'{m.group(1)}"{value}"'
            text = re.sub(rf'^((\s*{name})\s*=\s*)"(/path/to[^"]*)"',
                          swap, text, flags=re.MULTILINE)
        if text != original:
            path.write_text(text, encoding="utf-8")
    return rewritten


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def human(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def prepare_out(out: Path, force: bool) -> None:
    """Make an empty output directory, refusing to delete what we did not make."""
    if out.exists():
        if not (out / MARKER).exists() and not force:
            sys.exit(f"refusing to overwrite {out}: not a previous artifact build "
                     f"(no {MARKER}). Use --force, or pick another --out.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("built by tools/make_artifact.py\n", encoding="utf-8")


def check_programs(out: Path):
    """Report programs that compute nothing, their goal counts, and the paths a
    reviewer must set. Counted from the COPIED files, so the README describes
    the artifact rather than the checkout."""
    dud, paths, goals, printed = [], [], {}, {}
    for spec in PROGRAMS:
        text = (out / spec["path"]).read_text(encoding="utf-8")
        goals[spec["path"]] = len(GOAL_RE.findall(text))
        printed[spec["path"]] = len(PRINT_RE.findall(text))
        if not goals[spec["path"]]:
            dud.append(spec["path"])
        for name, value in DATASET_RE.findall(text):
            # Only placeholders are the reviewer's to set. A root that points
            # inside the artifact is already right.
            if value.startswith("/path/to"):
                paths.append((spec["path"], name, value))
    return dud, paths, goals, printed


def write_checksums(out: Path) -> int:
    lines, count = [], 0
    for item in sorted(out.rglob("*")):
        if item.is_file() and item.name not in {"SHA256SUMS", MARKER}:
            digest = hashlib.sha256(item.read_bytes()).hexdigest()
            lines.append(f"{digest}  {item.relative_to(out).as_posix()}")
            count += 1
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def _oracle_block(spec: dict) -> str:
    """The expected values for one experiment, or an honest note that we have none."""
    oracle = ORACLE.get(spec["path"])
    if not oracle:
        return ("**Expected values:** none recorded. This program is a pass/fail check; "
                "there is no number here to compare.\n")
    tol = oracle["tolerance"]
    how = ("compared **exactly** -- these do not move" if tol == 0
           else f"compared to within **{tol:g}**, except where a row says exact")
    rows = "\n".join(f"| `{name}` | `{value}` | {why} |"
                      for name, value, why in oracle["values"])
    return f"""**Expected values**, {how}.

| goal | our value | |
|---|---|---|
{rows}

*Where these come from:* {oracle['provenance']}

{oracle['note']}
"""


def write_readme(out: Path, paths: list[tuple[str, str, str]],
                 goals: dict[str, int], printed: dict[str, int],
                 with_model: bool) -> None:
    lock = out / "implementation/python/requirements.lock"
    n_locked = (len(re.findall(r"^[a-zA-Z].*==", lock.read_text(encoding="utf-8"),
                               re.MULTILINE)) if lock.exists() else 0)
    n_dataset_files = sum(1 for f in (out / DATASET_DEST).rglob("*") if f.is_file())
    """Write the reviewer's instructions: one numbered section per experiment.

    Generated rather than kept by hand, so it cannot drift from the manifest:
    the table, the commands and the goal counts all come from PROGRAMS and from
    the copied files themselves.
    """
    def _saved(s, goals, printed):
        """A `save` goal writes a file and prints nothing, so it would otherwise
        be counted as a line the reviewer never sees."""
        n = goals[s["path"]] - printed[s["path"]]
        return "" if not n else f" and {n} written by `save`"

    def _needs(s):
        if not s["needs_dataset"]:
            return "**none**"
        return "shipped cases + GPU" if s.get("needs_gpu") else "shipped cases"

    index_rows = "\n".join(
        f"| {i} | {s['title']} | {_needs(s)} | {s['minutes']} | {goals[s['path']]} "
        f"| {s['reproducibility']} "
        f"| {'yes, section C' if s['path'] in ORACLE else 'none (pass/fail)'} |"
        for i, s in enumerate(PROGRAMS, 1))

    sections = []
    for i, spec in enumerate(PROGRAMS, 1):
        expect = "\n".join(f"- {e}" for e in spec["expect"])
        data_line = ("This program needs **no data**. If it fails, the problem is the\n"
                     "install, not the dataset.\n"
                     if not spec["needs_dataset"] else
                     "It reads BraTS cases that ship in this artifact -- nothing to\n"
                     "download, nothing to edit. See section B.\n")
        if spec.get("nondeterminism"):
            data_line += "\n" + spec["nondeterminism"] + "\n"
        if spec.get("needs_gpu"):
            data_line += ("\nA CUDA GPU is assumed (`nnunet_device = \"cuda\"` in the\n"
                          "program). Set it to `\"cpu\"` to run without one, and expect it\n"
                          "to take very much longer.\n")
        cmd = f"./voxlogica run {spec['path']}"
        if spec.get("run_flags"):
            cmd += " " + spec["run_flags"]
        if spec.get("flags_note"):
            data_line += "\n" + spec["flags_note"] + "\n"
        sections.append(f"""### {i}. {spec['title']}

{spec['note']}

{data_line}
```bash
{cmd}
```

Expected: **exit status 0**, {printed[spec['path']]} named goal{'' if printed[spec['path']] == 1 else 's'} printed as JSON{_saved(spec, goals, printed)},
and {spec['total_files']} file{'' if spec['total_files'] == 1 else 's'} under
`{spec['outputs']}`. Time: {spec['minutes']}.

What a good run shows:

{expect}

Check the files:

```bash
find {spec['outputs'].split()[0]} -type f | wc -l     # {spec['total_files']}
```

If the count is lower, some writes were never demanded rather than attempted
and failed; see section D.

{_oracle_block(spec)}
""")

    model_section = (
        "**The program loads these weights; it does not train.** `nnunet.load_model`\n"
        "names the model that is already under `work_root` -- shipped as `model/`, and\n"
        "already written into the program -- and reads\n\n"
        "```\n"
        "model/nnUNet_results/Dataset900_BraTS2020WholeTumour/nnUNetTrainer__nnUNetPlans__3d_fullres/fold_0/\n"
        "```\n\n"
        "Two things follow:\n\n"
        "1. **The names must match.** That path is built from the program's\n"
        "   `nnunet_config` and `nnunet_trainer`, and the dataset folder comes from\n"
        "   `model/voxlogica_manifest.json`. Change one of them and the load fails\n"
        "   loudly -- `no completed fold under ...` -- rather than training for hours.\n"
        "2. **`postprocessing.pkl` is part of the model.** 260 bytes, under\n"
        "   `fold_0/validation/`. It records what nnU-Net determined on its own\n"
        "   validation set: keep only the largest connected component. Without it the\n"
        "   run still succeeds and predicts raw, which moves the aggregates by about\n"
        "   1e-2 and changes the threshold distribution. Do not delete it.\n\n"
        "`model/voxlogica_manifest.json` records what these weights are: dataset,\n"
        "trainer, configuration, folds, postprocessing. The network was trained on HGG\n"
        "positions 0-49 -- `BraTS20_Training_001` to `_050` -- which is why the sweep\n"
        "scores positions 50-69 and why those training cases are not shipped. Loading\n"
        "writes nothing: after experiment 4, `model/` is byte for byte what\n"
        "`SHA256SUMS` lists. Experiment 1 is the program that trains.\n"
        if with_model else
        "Not included in this build. Rebuild with `--with-model` to add it.\n")

    (out / "README.md").write_text(f"""# VoxLogicA-2 — TACAS artifact

Archived at **https://doi.org/{DOI}** (DOI `{DOI}`). Built from the repository
by `tools/make_artifact.py`; `PROVENANCE.json` records what went into this
build.

| # | experiment | needs | time | goals | reproducible | expected values |
|---|---|---|---|---|---|---|
{index_rows}

Read sections 0, A and B once, then run the experiments in order.
**Experiment 1 needs nothing but this directory** -- start there.

License: GPL-3.0, see `LICENSE`.
Source: https://github.com/VoxLogicA-Project/VoxLogicA-2

## Early light review (kick-the-tires) -- about 5 minutes, no dataset

**This section is the whole kick-the-tires phase.** It needs no BraTS, no GPU
and no large machine, and it is enough to tell whether the artifact is sound.
None of the four experiments has to run to pass it; they belong to the full
evaluation, and the shortest of them still takes about an hour.

```bash
# 1. the shipped files are intact
sha256sum -c SHA256SUMS | grep -vc ': OK$'                 # 0

# 2. the tool builds itself (downloads ~3 GB once; network needed here)
./voxlogica --help                                         # a usage message

# 3. every shipped program is well-formed against the engine that ships with it
for p in doc/gallery/programs/*/*.imgql; do
  ./voxlogica run --no-serve --no-execute "$p" && echo "ok $p"
done                                                       # ok, five times

# 4. the engine actually computes -- one second, no data
./voxlogica run --no-serve doc/gallery/programs/default/default-range-map.imgql
```

The last command must print exactly:

```
squares=[0.0, 1.0, 4.0, 9.0, 16.0, 25.0, 36.0, 49.0, 64.0, 81.0, 100.0, 121.0, 144.0, 169.0, 196.0, 225.0, 256.0, 289.0, 324.0, 361.0]
```

```bash
# 5. the measurement suite runs -- about 3 seconds, still no data
python3 tools/perf/suite.py --hold 0 --reps 1 --warm-runs 1 --experiments cache \\
    --program doc/gallery/programs/default/default-range-map.imgql \\
    --out perf/smoke --work-dir perf/work
python3 tools/perf/report.py perf/smoke
```

Three runs, then a two-row table whose `Problems` section reads `- none`. That
is the apparatus the paper's performance figures were taken with; section G
says how to point it at a real experiment.

Step 3 parses, reduces and type-checks each program without computing anything;
step 4 computes; step 5 measures. Together they show that the environment is
complete, that the engine works end to end, and that the measurements in the
paper can be retaken here.

If all five steps pass, the artifact is ready for the full evaluation. A good
next move is to start experiment 1 in the background -- it needs no dataset
either, but takes 51 minutes on 24 cores and 77 on 4, at 1.2 GB of memory --
and meanwhile read section B -- short, now that the data ships.

## Resources

| | |
|---|---|
| Disk | 25 GB free (venv 8 GB, ~5 GB of results store per sweep; the artifact itself, image data included, is under 500 MB) |
| RAM, measured peak | depends on parallelism. At 24 threads: experiment 2: 8.8 GB; 3: 4.5 GB. At 4 threads (`--threads 4`): experiment 1: 1.2 GB; 2: 1.9 GB; 3: 2.4 GB. Experiment 4 is run at `--threads 4`, as section C gives it, and peaks at **7.6 GB**. A VM with 8 GB runs experiments 1, 2 and 3; **16 GB runs all four** |
| CPU | any; the engine uses every core it finds. Times in this README are from a 24-core machine |
| GPU | **experiment 4 only** (`nnunet_device = "cuda"`). Without one it can run on CPU -- set the device to `"cpu"` -- but we have not timed that |
| Network | once, during bootstrap; every download is version-pinned and hash-checked |
| OS | Linux x86-64 is what we measured on. `bootstrap.py` fetches its own interpreter, so the host Python does not matter |

In a virtual machine: experiments 1, 2 and 3 fit, measured in an Ubuntu 22.04
container with 4 cores, 8 GB and no GPU -- experiment 1 in 77 min, 2 in 31 s,
3 in 3 min.

Experiment 4 needs more: at `--threads 4`, the configuration section C gives,
it peaks at **7.6 GB**, so **16 GB is enough** and 8 GB leaves no margin. The
peak is the sweep's own working set -- twenty percentile-ranked volumes and
twenty-one candidate segmentations each -- so the ask is a 16 GB machine with a
CUDA device, not a large one.

---

## 0. Check the shipped files -- before you touch anything

```bash
sha256sum -c SHA256SUMS
```

Every line must end in `OK`. It covers the engine, the programs, the weights
and the image data. Nothing in the experiments writes back into these files --
runs write under `output/` and into their results store -- so the check stays
informative afterwards as well.

## A. Build the tool

```bash
./voxlogica --help
```

The first call runs `bootstrap.py`, which downloads a pinned `uv`, reads
`.python-version` and creates `.venv` here. Measured: 137 packages in 44 s on
a fast connection. It is idempotent: re-run it freely.

**Disk: have 25 GB free before you start.** The venv is 8 GB (torch and its
CUDA libraries are most of it) and each of the two sweeps writes a ~5 GB results
store. If space runs short the engine warns `cache budget cut from ... by free
space`; check free space and rerun.

Every version is pinned. `implementation/python/requirements.lock` resolves the
whole transitive graph -- {n_locked} packages, each with its sha256 -- and that
is what gets installed. A package re-uploaded under the same version fails the
install rather than substituting itself. The last line of the bootstrap output
says which path was taken; it must read

```
Environment synchronized with the hashed lock files.
```

If it instead mentions `requirements.txt (NO LOCK FILE ...)`, the lock did not
travel with this artifact and the resolved versions may differ from ours.

Two requirements:

1. **Free-threaded CPython.** `.python-version` asks for `3.14t`. The engine
   refuses a stock build rather than running serialized, and says so.
2. **Always launch through `./voxlogica`.** It exports `PYTHON_GIL=0` before
   the interpreter starts (too late to set from Python) and puts `.venv/bin` on
   `PATH`, which is where nnU-Net's own executables live. Calling
   `python -m voxlogica.main` directly appears to work and then fails inside
   nnU-Net with `No such file or directory: nnUNetv2_plan_and_preprocess`.

## B. The BraTS cases

They ship with the artifact. Nothing to download, nothing to edit.

| what | where | read by |
|---|---|---|
| the twenty scored cases, `BraTS20_Training_051` to `_070`, five volumes each | `data/MICCAI_BraTS2020_TrainingData/` | experiments 3 and 4 |
| `name_mapping.csv`, the metadata the population is selected from | the same directory | experiments 3 and 4 |
| the five-case sample | `doc/gallery/programs/brats2020/data/` | experiment 2 |

The twenty are HGG positions 50-69 of the official training set. Both sweeps
read the grade and the id out of `name_mapping.csv`, keep the HGG rows and
slice, so a case is in the study because the metadata says so -- not because of
what happens to be on disk. That file lists all 369 cases, so those positions
are positions in the full dataset. The other 344 cases are not here: no shipped
program names them, and the ones that trained the network (positions 0-49) are
not needed to use it -- see section F.

**These are the official cases, unmodified**, redistributed here with the
permission of the BraTS organisers. To see that rather than take it:

```bash
python3 tools/dataset_manifest.py data/MICCAI_BraTS2020_TrainingData --subset
```

Expected: `OK: the {n_dataset_files} files here are the ones the expected values
were measured on.` and exit status 0. `tools/brats2020-manifest.json` is the
contract it checks against -- all 369 cases of the official set, each volume
identified by its DECOMPRESSED content, so the check also passes on a copy
compressed at another level.

To run the sweeps against your own copy of the full training set instead, point
`dataset_root` at it -- one line near the top of each of the two programs -- and
run the same check without `--subset` to see whether that copy is the one these
numbers came from.

If you use these cases outside this evaluation, cite the challenge: Menze et
al., *IEEE TMI* 34(10), 2015; Bakas et al., *Nature Scientific Data* 4, 2017;
Bakas et al., arXiv:1811.02629, 2018.

## C. Run the experiments

{"".join(sections)}
---

## C.5 Reading experiments 3 and 4 together

Both score the SAME twenty cases -- HGG positions 50-69, which are
`BraTS20_Training_051` to `_070` -- and both build the segmentation with the
same definitions: `preprocess`, `segment`, `sweep_of`, `dice`, `vi_thresholds`,
`published_vi_thr` and `whole_tumour` are identical in the two programs. So the
three pairings below are comparable, and together they are the paper's claim:

| | where | mean | median |
|---|---|---|---|
| threshold sweep vs the **annotation** | exp 3, `dice_best_mean` | 0.8555 | 0.9074 |
| nnU-Net vs the **annotation** | exp 4, `model_dice_mean` | 0.8588 | 0.9152 |
| threshold sweep vs **nnU-Net** | exp 4, `dice_best_mean` | 0.8502 | 0.9259 |

The twenty cases are outside the network's training set (positions 0-49), so
neither method is being scored on data it was fitted to.

**A trap worth naming.** The two programs print nineteen goal names in common,
and eighteen of them MEAN DIFFERENT THINGS -- only `n_cases` does not --
because each program defines `truth_of` for itself:

| | `truth_of(i)` is |
|---|---|
| experiment 3 | the annotation BraTS ships |
| experiment 4 | what nnU-Net predicted |

Everything downstream follows: `best_dice`, `best_vi_thr`, the `dice_best_*`,
`dice_fixed_*` and `vi_thr_*` families, and -- ranked by a different score --
`outlier_cases`, `outlier_dice`, `outlier_paths` and the exported planes. So
`dice_best_mean` is 0.8555 in
experiment 3 and 0.8502 in experiment 4, on the same twenty cases, and these
are two different quantities, not a discrepancy. The two experiments also
export DIFFERENT ten worst cases, for the same reason. Experiment 4's name for
"against the annotation" is `model_dice_mean`; experiment 3 needs no separate
name because the annotation is the only reference it uses.

**The exported PNGs are numbered by POSITION, not by BraTS id -- and the two
programs do not use the same position.**

| | a file name | what it is |
|---|---|---|
| experiment 3 | `output/aiim-sweep/case_001_*` | position 1 of the TWENTY, i.e. `BraTS20_Training_052` |
| experiment 4 | `output/aiim-sweep-nnunet/case_053_*` | position 53 of the 293 HGG, i.e. `BraTS20_Training_054` |

Neither number is the BraTS id; `outlier_paths` prints the real path of every
case either program exported, and that is the mapping to use. Experiment 4 also
writes four images per plane rather than three: `_flair`, `_predicted` (the
sweep), `_nnunet` (the reference it was scored against) and `_truth` (the
annotation, exported so a reader can judge both).

---

## D. Two behaviours worth knowing about

**A second run finishes instantly and writes no files.** Results are cached in
`~/.voxlogica`, keyed by content, so re-running an unchanged program returns
the stored values without recomputing -- and so without re-writing the images.
A warm store changes the time, not the answer.

To compute everything again, give the run a store of its own:

```bash
./voxlogica run <program> --store-db /path/to/a/new/store.db
```

That is how the numbers in section C were measured. The store is 5-7 GB for
either BraTS experiment and must not be on a tmpfs (so not under `/tmp` on
most systems): a store in RAM competes with the memory the engine budgets
from.

**Do not run experiment 1 twice at the same time.** It puts its training tree
in a fixed directory under `/tmp`, so two concurrent runs share it and corrupt
each other's preprocessing. Run them one after another. Experiment 4 writes
nothing outside `output/` and its store: it loads the shipped weights.

## E. Reproducibility

`--threads N` fixes the degree of parallelism. Results are **bit-identical**
across thread counts -- verified at 1, 4 and 16 threads on experiment 3, and
identical again to a warm-cache run. Only the wall time changes. So a number
that differs on your machine is not the scheduler.

The nnU-Net programs are the exception. Experiment 1 trains, so its output is
not reproducible bit-for-bit at all. Experiment 4 uses the shipped weights and
only predicts with them, which puts it within 1e-4 -- nnU-Net's sliding-window
inference on a GPU is not bitwise deterministic -- with the winning thresholds
identical every time.

## F. The trained model

{model_section}

## G. Measuring performance yourself

The paper's performance figures were taken with the suite that ships here. It
measures from OUTSIDE the engine -- wall clock, processor time and peak
resident memory come from `os.wait4` -- and reports the engine's own counters
beside those numbers rather than instead of them.

| file | what it does |
|---|---|
| `tools/perf/suite.py` | runs one program under a set of experiments, repeated and interleaved |
| `tools/perf/report.py` | one Markdown table per results directory, plus `summary.json` |
| `tools/perf/vl1_compare.py` | the same measurements for VoxLogicA 1 and VoxLogicA 2 on the sweep |

It needs only the standard library, so it runs under your system `python3`;
what it measures is the `.venv` that section A built, so do section A first.

```bash
python3 tools/perf/suite.py \\
    --program doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql \\
    --out perf/aiim --work-dir perf/work \\
    --experiments scaling,cache --threads-list 1,4,8,24 --reps 3
python3 tools/perf/report.py perf/aiim
```

`--experiments` takes any of `scaling` (each width in `--threads-list`),
`fusion` (element-wise fusion on and off), `cache` (a cold run, then runs
against the store it left) and `resume` (a run interrupted with the signal
Ctrl-C sends, then restarted on that store). Repetitions are interleaved, so
thermal drift spreads over every configuration instead of loading the last.

**What the report flags.** Every run's goals are hashed, and runs of the same
program that disagree appear under `Problems` -- as do a non-zero exit, and a
run during which other processes took more than `--max-foreign` percent of a
core. On a busy machine, `--quiet-pct` and `--hold` make the suite wait for
quiet before each run.

`--work-dir` must not be on tmpfs: a store in RAM competes with the memory the
engine budgets from. Each run gets a fresh store there, removed afterwards
unless you pass `--keep-stores`. The engine's per-machine calibration is
isolated per suite run, so an earlier session cannot change ITK's thread count
between two measurements.

**Measuring a variant.** `--set NAME=VALUE` overrides a top-level definition
before the run. String values keep their quotes, and the shell must not eat
them:

```bash
--set 'dataset_root="data/MICCAI_BraTS2020_TrainingData"' --set case_count=2
```

The program as run is written to `program.imgql` beside the results, overrides
applied. An undefined name is an error, not a silent no-op.

**VoxLogicA 1.** `vl1_compare.py` needs a VoxLogicA 1 binary of your own (we
used 1.3.3). VoxLogicA 1 has no loops or sequences, so the script writes the
sweep out for it one case at a time, writes the VoxLogicA 2 programs from the
same template, and compares both tools' answers value by value before
reporting any timing. Pass `--first-case 51` for the twenty cases the
experiments use; it defaults to case 001.

Your numbers will not be ours -- these were taken on 24 cores. The shape is
the claim: how the time moves with the width, and what the cache is worth.
""", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the TACAS artifact directory.")
    ap.add_argument("--out", default=str(REPO.parent / "voxlogica-tacas-artifact"),
                    help="output directory (default: beside the repository)")
    ap.add_argument("--with-model", action="store_true",
                    help="include the trained nnU-Net weights (~240 MB)")
    ap.add_argument("--model-source", type=Path, default=None,
                    help=f"nnU-Net work_root the weights come from "
                         f"(default: {MODEL_SOURCE})")
    ap.add_argument("--dataset-source", type=Path, default=None,
                    help=f"BraTS 2020 training directory the shipped cases come from "
                         f"(default: {DATASET_SOURCE})")
    ap.add_argument("--tar", action="store_true", help="also write a .tar.gz beside it")
    ap.add_argument("--zip", action="store_true",
                    help="also write a .zip beside it (EasyChair wants a ZIP)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite --out even if this script did not build it")
    args = ap.parse_args()

    if args.model_source:
        src = args.model_source.expanduser().resolve()
        MODEL["source"] = src / "nnUNet_results"
        MODEL["manifest"] = src / "voxlogica_manifest.json"


    out = Path(args.out).expanduser().resolve()
    started = time.time()
    print(f"repository : {REPO}\nartifact   : {out}")

    step(1, f"Preparing {out}")
    prepare_out(out, args.force)

    step(2, f"Copying the engine ({len(ENGINE)} entries)")
    files = size = 0
    for rel in ENGINE:
        src = REPO / rel
        if not src.exists():
            sys.exit(f"missing from the checkout: {rel}")
        f, s = copy_tree(src, out / rel)
        files += f
        size += s
        print(f"    {rel}  ({f} files, {human(s)})")

    step(3, f"Copying {len(PROGRAMS)} programs")
    for spec in PROGRAMS:
        src = REPO / spec["path"]
        if not src.exists():
            sys.exit(f"missing from the checkout: {spec['path']}")
        f, s = copy_tree(src, out / spec["path"])
        files += f
        size += s
        print(f"    {spec['title']}: {spec['path']}")

    step(4, f"Copying the measurement suite ({len(MEASUREMENT)} files)")
    for rel in MEASUREMENT:
        src = REPO / rel
        if not src.exists():
            sys.exit(f"missing from the checkout: {rel}")
        f, sz = copy_tree(src, out / rel)
        files += f
        size += sz
        print(f"    {rel}  ({human(sz)})")
    # The programs get type-checked in step 7; nothing else would notice if one
    # of these three shipped broken, so compile them here. In memory: writing
    # .pyc files into the artifact would add files the checksums never list.
    for rel in MEASUREMENT:
        try:
            compile((out / rel).read_text(encoding="utf-8"), rel, "exec")
        except SyntaxError as exc:
            sys.exit(f"the measurement suite does not compile: {rel}: {exc}")
    print(f"    all {len(MEASUREMENT)} compile under Python "
          f"{sys.version_info.major}.{sys.version_info.minor}")

    step(5, "Copying the five-case sample")
    sample_src = REPO / SAMPLE_DEST
    data_dir = out / SAMPLE_DEST
    data_dir.mkdir(parents=True, exist_ok=True)
    cases = sorted(d.name for d in sample_src.iterdir() if d.is_dir()) \
        if sample_src.exists() else []
    if not cases:
        sys.exit(f"no case directories under {sample_src}: experiment 2 reads whatever "
                 f"is in its own data directory, and would score nothing")
    sample_size = 0
    for case in cases:
        f, s = copy_tree(sample_src / case, data_dir / case)
        files += f
        sample_size += s
    size += sample_size
    (data_dir / "README.md").write_text(
        "# The five-case sample\n\n"
        "Five cases of the BraTS 2020 training set, as BraTS ships them -- one\n"
        "directory each, five volumes inside (`_flair`, `_t1`, `_t1ce`, `_t2`,\n"
        "`_seg`):\n\n"
        + "".join(f"- `{c}`\n" for c in cases)
        + "\nThe program reads them with `dir(..., recursive)` and scores them in\n"
          "listing order, so the file suffixes matter and the directory names do not.\n"
          "Replacing them with five other cases is a valid thing to do; the expected\n"
          "values in the README are then no longer the ones to compare against.\n",
        encoding="utf-8")
    print(f"    {len(cases)} cases ({human(sample_size)}): {', '.join(cases)}")

    step(6, "Copying the BraTS cases the sweeps score")
    dataset_src = (args.dataset_source.expanduser().resolve()
                   if args.dataset_source else DATASET_SOURCE)
    if not (dataset_src / "name_mapping.csv").is_file():
        sys.exit(f"no BraTS 2020 training directory at {dataset_src} "
                 f"(no name_mapping.csv); point --dataset-source at one")
    scored = scored_cases(dataset_src)
    dataset_dir = out / DATASET_DEST
    dataset_dir.mkdir(parents=True, exist_ok=True)
    # The metadata first: both sweeps select their population from it, and it is
    # also what makes the shipped slice legible -- the ids in it are the full 369,
    # so the positions the programs name are the positions of the official set.
    f, dataset_size = copy_tree(dataset_src / "name_mapping.csv",
                                dataset_dir / "name_mapping.csv")
    files += f
    for case in scored:
        case_src = dataset_src / case
        if not case_src.is_dir():
            sys.exit(f"{case} is in the window the programs score but is not in "
                     f"{dataset_src}")
        f, s = copy_tree(case_src, dataset_dir / case)
        files += f
        dataset_size += s
    size += dataset_size
    print(f"    name_mapping.csv, {len(scored)} cases {scored[0]} .. {scored[-1]} "
          f"({human(dataset_size)})")

    # Now that the data is in, the programs can name it. Nothing left to edit.
    for program, name, value in point_at_shipped_data(out):
        print(f"    {Path(program).name}: {name} -> {value}")

    step(7, "Trained model")
    if args.with_model:
        src = MODEL["source"]
        if not src.exists():
            sys.exit(f"--with-model asked for, but {src} does not exist")

        # WHAT THE WEIGHTS SAY THEY ARE, from the run that produced them.
        shipped = {}
        manifest = MODEL["manifest"]
        if manifest.exists():
            m = json.loads(manifest.read_text(encoding="utf-8"))
            shipped = {"trainer": m.get("trainer", ""),
                       "configuration": m.get("configuration", "")}
            dataset_folder = m.get("dataset_folder", "") or m.get("dataset_name", "")
            sel = (m.get("selection") or [{}])[0]
            shipped["trainer"] = shipped["trainer"] or sel.get("trainer", "")

        # WHAT THE SHIPPED PROGRAMS ASK FOR.
        wanted, consumers = {}, []
        for spec in PROGRAMS:
            text = (out / spec["path"]).read_text(encoding="utf-8")
            found = {k: (r.search(text).group(1) if r.search(text) else "")
                     for k, r in MODEL_SETTINGS_RE.items()}
            if any(found.values()):
                consumers.append((spec["path"], found))
                wanted = found

        if not consumers:
            sys.exit("--with-model asked for, but no shipped program names an "
                     "nnU-Net model. The weights would be 236 MB nothing can use.")

        mismatch = {k: (wanted[k], shipped.get(k, "")) for k in MODEL_SETTINGS_RE
                    if shipped and wanted[k] != shipped.get(k, "")}
        for path, found in consumers:
            print(f"    consumer: {Path(path).name}")
        if shipped:
            print(f"    shipped weights: {dataset_folder} / "
                  f"{shipped['trainer']} / {shipped['configuration']}")
        if mismatch:
            detail = "; ".join(f"{k}: program wants {w!r}, weights are {g!r}"
                               for k, (w, g) in mismatch.items())
            sys.exit("the weights do not match what the shipped program asks for, "
                     "so loading them would fail:\n  " + detail +
                     "\nAlign the program's constants with the weights (or "
                     "retrain), then rebuild.")

        kept = 0
        for item in sorted(src.rglob("*")):
            if item.is_file() and item.name in MODEL["keep"]:
                target = out / MODEL["dest"] / item.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                kept += 1
                files += 1
                size += target.stat().st_size
                print(f"    {item.relative_to(src)}  ({human(target.stat().st_size)})")
        if manifest.exists():
            # The manifest records where the weights were trained, as absolute
            # paths on the build machine. The engine recomputes `trainer_dir`
            # from the reviewer's own `work_root` on every run, so these are
            # history, not configuration -- and history that names a stranger's
            # home directory. Rewrite them to where the weights now live.
            text = manifest.read_text(encoding="utf-8")
            before = text
            text = text.replace(str(MODEL_SOURCE) + "/", "model/")
            text = text.replace(str(MODEL_SOURCE), "model")
            target = out / "model" / manifest.name
            target.write_text(text, encoding="utf-8")
            if text != before:
                print(f"    {manifest.name}: rewrote the build machine's paths")
            kept += 1
        print(f"    kept {kept} files; refused {', '.join(MODEL['refused'])} "
              f"(BraTS-derived volumes)")
    else:
        print("    skipped (pass --with-model to include ~240 MB of weights)")

    step(8, "Checking the programs")
    shipped = {spec["path"] for spec in PROGRAMS}
    orphaned = sorted(set(ORACLE) - shipped)
    if orphaned:
        sys.exit("ORACLE has expected values for programs this artifact does not "
                 "ship:\n  " + "\n  ".join(orphaned))
    dud, paths, goals, printed = check_programs(out)
    for program, name, value in paths:
        print(f"    {Path(program).name}: {name} = {value}")
    if dud:
        sys.exit("these programs have no print/save goal and would compute "
                 "nothing:\n  " + "\n  ".join(dud))
    print("    " + ", ".join(f"{Path(s['path']).name}: {goals[s['path']]} goals"
                             for s in PROGRAMS))
    if paths:
        sys.exit("these shipped programs still name a placeholder path, while the "
                 "README tells the reviewer there is nothing to edit:\n  "
                 + "\n  ".join(f"{program}: {name} = {value}"
                                for program, name, value in paths))
    print("    no placeholder paths left: every root names something in the artifact")

    step(9, "Writing README.md and PROVENANCE.json")
    write_readme(out, paths, goals, printed, args.with_model)
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    (out / "PROVENANCE.json").write_text(json.dumps({
        "doi": DOI,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "built_by": "tools/make_artifact.py",
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_describe": git("describe", "--always", "--dirty"),
        "working_tree_dirty": dirty,
        "python_version_file": (REPO / ".python-version").read_text(encoding="utf-8").strip(),
        "builder_python": sys.version.split()[0],
        "programs": [s["path"] for s in PROGRAMS],
        "includes_model": args.with_model,
        "includes_dataset": True,
        "dataset_cases": scored,
        "dataset_sample_cases": cases,
    }, indent=2) + "\n", encoding="utf-8")
    if dirty:
        print("    WARNING: working tree has uncommitted tracked changes; "
              "the commit in PROVENANCE.json does not describe what was copied")

    step(10, "Writing SHA256SUMS")
    print(f"    {write_checksums(out)} files hashed")

    tarball = None
    if args.tar:
        step(11, "Writing the tarball")
        tarball = out.with_suffix(".tar.gz")
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(out, arcname=out.name)
        print(f"    {tarball}  ({human(tarball.stat().st_size)})")

    zipball = None
    if args.zip:
        step(12 if args.tar else 11, "Writing the zip")
        import zipfile
        zipball = out.with_suffix(".zip")
        with zipfile.ZipFile(zipball, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(out.rglob("*")):
                if item.is_file():
                    zf.write(item, (out.name / item.relative_to(out)).as_posix())
        print(f"    {zipball}  ({human(zipball.stat().st_size)})")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    count = sum(1 for f in out.rglob("*") if f.is_file())
    print(f"\n{'=' * 62}\n"
          f"  artifact   {out}\n"
          f"  files      {count}\n"
          f"  size       {human(total)}\n"
          f"  model      {'included' if args.with_model else 'not included'}\n"
          f"  dataset    {len(scored) + len(cases)} BraTS cases "
          f"({human(dataset_size + sample_size)})\n"
          + (f"  tarball    {tarball}\n" if tarball else "")
          + (f"  zip        {zipball}\n" if zipball else "")
          + f"  built in   {time.time() - started:.1f} s\n"
          f"{'=' * 62}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
