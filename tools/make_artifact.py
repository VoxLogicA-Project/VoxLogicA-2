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
    # The container recipe. The image itself is published beside this archive --
    # it is 8.6 GB -- and this is what it is built from, so a reviewer can read
    # what went in and rebuild it.
    "Dockerfile",
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
    # Not an experiment: the one-second program the smoke test runs to
    # see the engine COMPUTE something, rather than only parse and typecheck.
    "doc/gallery/programs/default/default-range-map.imgql",
    # The hello world, with its input: the shortest program that reads an image,
    # computes something over it and writes a result. It is what someone starts
    # from to write their own, which is why it ships and has a section of its own
    # in the README.
    "doc/gallery/programs/hello/hello-greyscale.imgql",
    "doc/gallery/programs/hello/hello-input.png",
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
        "minutes": "7 min on four cores; allow a couple of hours, depending on "
                   "the host",
        "note": "The program generates circles and squares, trains a small nnU-Net on "
                "the CPU and predicts with it. A green run exercises the whole "
                "install: engine, SimpleITK, torch, nnU-Net. Start it in the "
                "background and read on.",
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
                "against the ground truth, on five cases.",
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
        "note": "The AIIM study as one program: sweep the permissive threshold over "
                "0.72-0.92, keep the best per case, report the spread of the winning "
                "thresholds. Scored on HGG cases 051-070, the same twenty experiment 4 "
                "evaluates.",
        "outputs": "output/aiim-sweep/",
        "expect": [
            "`dice_fixed_mean`, `dice_fixed_median`, `dice_fixed_stdev` -- the single "
            "published threshold, scored",
            "`dice_best_mean`, `dice_best_median`, `dice_best_stdev` -- the per-case best",
            "`fixed_dice` and `best_dice` -- the twenty per-case scores behind the "
            "two families above",
            "`vi_thr_distribution` -- which thresholds won, and how often",
            "`case_start` -- 50, which says which twenty cases were scored",
            "90 PNGs under `output/aiim-sweep/`, nine for each of the ten worst "
            "cases: three planes, and for each plane the FLAIR, the segmentation and "
            "the ground truth -- grey anatomy with a white mask on the last two",
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
                "in place of the annotation. It does not train: `nnunet.load_model` "
                "names the weights in `model/`.",
        "nondeterminism": (
            "**Reproducible to 1e-4.** GPU inference is not bitwise deterministic: "
            "across six runs the per-case Dice values moved by at most 1.9e-05 and the "
            "winning thresholds were identical every time. Compare "
            "`vi_thr_distribution` and `best_vi_thr` exactly, Dice to 1e-4."),
        "run_flags": "--threads 4",
        "flags_note": (
            "**Run this one with `--threads 4`**, as the command below does: it is the "
            "configuration these figures were taken at, and it peaks at 7.6 GB. The 34 "
            "goals do not depend on the width."),
        "outputs": "output/aiim-sweep-nnunet/",
        "expect": [
            "`model_dice_mean` -- how close the learned reference is to the "
            "annotation it replaced. Read this first: it bounds everything below",
            "`vi_thr_distribution` -- as in experiment 3, against the learned "
            "reference",
            "`best_dice`, `best_vi_thr` -- per case",
            "`dice_fixed_mean`, `dice_fixed_median`, `dice_fixed_stdev` and the "
            "per-case `fixed_dice` -- the single published threshold, scored",
            "120 PNGs under `output/aiim-sweep-nnunet/`, twelve for each of the ten "
            "worst cases: three planes, and per plane `_flair`, `_predicted` (the "
            "sweep), `_nnunet` (the learned reference) and `_truth` (the annotation) "
            "-- grey anatomy with a white mask on the last three. Where a mask is "
            "empty the overlay is the FLAIR itself, so a few of the 120 coincide",
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
        "note": "**A Dice of exactly 0.000 on case 079 is the expected result**: the "
                "smallest tumour in BraTS raises no seed, so there is nothing to grow. "
                "If you get 0.000 there, you have reproduced our result.",
    },
    "doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql": {
        "provenance": "Nine runs, bit-identical throughout: on an empty store, at 1, 4 and 24 threads, warm, and under the lazy strategy.",
        "tolerance": 0.0,
        "values": [
            ("case_start", "50.0", "HGG positions 50-69, i.e. cases 051-070"),
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
             "eight thresholds win across twenty cases"),
        ],
        "note": "A difference here is a real difference, not scheduling noise; start "
                "with your SimpleITK version.",
    },
    "doc/gallery/programs/nnunet/brats-threshold-sweep-nnunet.imgql": {
        "provenance": "Six cold-store runs on the same weights and the same 20 cases, "
                      "one of them on this artifact as a reviewer would run it. The "
                      "values are the run of 2026-10-12; the others sit within 1.9e-05 "
                      "of them, with the winning thresholds identical in all six.",
        "tolerance": 1e-4,
        "values": [
            ("model_dice_mean", "0.8587657935069911",
             "how close the learned reference is to the annotation"),
            ("model_dice_median", "0.9152163651578276", ""),
            ("model_dice_stdev", "0.11011142897334406", ""),
            ("model_sensitivity_mean", "0.821302951428539", "per case 0.38-0.98"),
            ("model_specificity_mean", "0.9994939912279985", "whole-volume; read differences"),
            ("dice_fixed_mean", "0.8069294373520195", ""),
            ("dice_fixed_median", "0.8846858248141457", ""),
            ("dice_fixed_stdev", "0.22969636865562157",
             "one case scores 0.000"),
            ("sensitivity_fixed_mean", "0.9164727345951725", ""),
            ("specificity_fixed_mean", "0.9979484205242406", ""),
            ("dice_best_mean", "0.850164485800129", ""),
            ("dice_best_median", "0.9258509606139985", ""),
            ("dice_best_stdev", "0.18727226965618532", ""),
            ("sensitivity_best_mean", "0.9431586991502892", ""),
            ("specificity_best_mean", "0.9973265001914303", ""),
            ("vi_thr_mean", "0.8945000000000001", "**exact**, no tolerance"),
            ("vi_thr_median", "0.9", "**exact**"),
            ("vi_thr_stdev", "0.041986840043543486", "**exact**"),
            ("vi_thr_distribution", "[[0.73,1],[0.87,2],[0.89,4],[0.9,4],[0.91,2],[0.92,7]]",
             "**exact**"),
        ],
        "note": "The winning thresholds came back identical in every run. Report Dice, "
                "sensitivity and specificity to 3-4 decimals; past that is noise.\n\n"
                "**Specificity is over the whole volume**, as BraTS scores it, so it "
                "sits near 1 for any plausible segmentation: read the differences "
                "between rows.\n\n"
                "**The population is the 293 HGG cases** of `name_mapping.csv`; the 20 "
                "evaluated are positions 50-69, cases 051-070.",
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
        return "**Expected values:** none. This program is a pass/fail check.\n"
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
    #: The image is published per artifact tag, so the commands name the tag the
    #: README was generated from rather than a moving `latest`.
    image_tag = (git("describe", "--tags", "--abbrev=0") or "latest").replace(
        "tacas-artifact-", "")
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
                     "It reads BraTS cases shipped here; see section B.\n")
        if spec.get("nondeterminism"):
            data_line += "\n" + spec["nondeterminism"] + "\n"
        if spec.get("needs_gpu"):
            data_line += ("\nA CUDA GPU is assumed (`nnunet_device = \"cuda\"` in the\n"
                          "program), and it uses 2.5 GB of GPU memory -- measured, so a card\n"
                          "with about 3 GB free is enough. The device can be set to `\"cpu\"`,\n"
                          "but a test run of ours spent 90 minutes without finishing a single\n"
                          "prediction, so we cannot say what that path costs.\n")
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

Expected: **exit status 0**, {printed[spec['path']]} goal{'' if printed[spec['path']] == 1 else 's'} printed as `name=value` lines{_saved(spec, goals, printed)},
then a JSON summary of the run, and {spec['total_files']} file{'' if spec['total_files'] == 1 else 's'} under
`{spec['outputs']}`. Time: {spec['minutes']}.

What a good run shows:

{expect}

Check the files:

```bash
find {spec['outputs'].split()[0]} -type f | wc -l     # {spec['total_files']}
```

A lower count means a warm store returned the values without re-writing the
files; see section D.

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
        "   with `no completed fold under ...`.\n"
        "2. **`postprocessing.pkl` is part of the model.** 260 bytes, under\n"
        "   `fold_0/validation/`. It records what nnU-Net determined on its own\n"
        "   validation set: keep only the largest connected component. Without it the\n"
        "   run still succeeds and predicts raw, which moves the aggregates by about\n"
        "   1e-2 and changes the threshold distribution. Do not delete it.\n\n"
        "`model/voxlogica_manifest.json` records what these weights are: dataset,\n"
        "trainer, configuration, folds, postprocessing. The network was trained on HGG\n"
        "positions 0-49, `BraTS20_Training_001` to `_050`, which is why the sweep\n"
        "scores positions 50-69. Loading writes nothing: after experiment 4, `model/`\n"
        "is byte for byte what `SHA256SUMS` lists.\n"
        if with_model else
        "Not included in this build. Rebuild with `--with-model` to add it.\n")

    (out / "README.md").write_text(f"""# VoxLogicA-2 — TACAS artifact

Archived at **https://doi.org/{DOI}**. Built from the repository by
`tools/make_artifact.py`; `PROVENANCE.json` records what went into this build.

| # | experiment | needs | time | goals | reproducible | expected values |
|---|---|---|---|---|---|---|
{index_rows}

Read sections 0, A and B once, then run the experiments in order. For the early
light review, the smoke test is the next section.

License: GPL-3.0, see `LICENSE`, which covers the code here. The BraTS 2020
cases under `data/` are the challenge's; section B says what to cite. The
weights under `model/` we trained on those cases.
Source: https://github.com/VoxLogicA-Project/VoxLogicA-2

## Badges, requirements, platforms

**Badges applied for: Available and Reusable**, and Functional, which Reusable
subsumes.

**Additional requirements.** No proprietary software and no licence to accept;
everything installed comes from pinned, hash-checked versions. Experiment 4
needs two things the other three do not:

- **A CUDA GPU with about 3 GB free**: it uses 2.5 GB, measured.
- **16 GB of RAM**: it peaks at 7.6 GB. Experiments 1, 2 and 3 fit in 8 GB with
  no GPU.

Network access is needed once, during `./voxlogica --help` (section A), which
downloads about 3 GB of pinned packages. Nothing else reaches the network, and
the container image needs no network at all.

**Platforms tested.** Linux **amd64** (x86-64) only:

| | |
|---|---|
| all four experiments | the container image, from the archive beside this one |
| all four experiments | Ubuntu 26.04, kernel 7.0, 24 cores, 61 GB, one CUDA GPU |
| the smoke test and experiments 1, 2 and 3 | Ubuntu 25.04 in a container, 4 cores, 8 GB, no GPU |
| experiments 1, 2 and 3 | Ubuntu 22.04 in a container, 4 cores, 8 GB, no GPU |

Not tried on arm64, macOS or Windows: the lock was resolved against the Linux
x86-64 wheels. `bootstrap.py` fetches its own interpreter, so the host's Python
does not matter.

**Packaging.** Plain files: unpack and run in place. One build command
(`./voxlogica --help`, section A) and one run command per experiment
(section C). A container image is published beside this archive; see the next
section.

**If you have no GPU**, experiments 1, 2 and 3 are the subset to run: about a
quarter of an hour together, and they carry the paper's symbolic results.

## Smoke test (early light review) -- about 5 minutes, no dataset

**These six steps are the whole smoke test.** They need no BraTS, no GPU
and no large machine, and they are enough to tell whether the artifact is sound.
None of the four experiments has to run to pass it; they belong to the full
evaluation, where the shortest of them is experiment 2, at half a minute.

```bash
# 1. the shipped files are intact
sha256sum -c SHA256SUMS | grep -vc ': OK$'                 # 0

# 2. the tool builds itself (downloads ~3 GB once; network needed here)
./voxlogica --help                                         # a usage message

# 3. every shipped program is well-formed against the engine that ships with it
for p in doc/gallery/programs/*/*.imgql; do
  ./voxlogica run --no-serve --no-execute "$p" && echo "ok $p"
done                                                       # ok, six times

# 4. the engine actually computes -- one second, no data
./voxlogica run --no-serve doc/gallery/programs/default/default-range-map.imgql
```

The last command must print exactly:

```
squares=[0.0, 1.0, 4.0, 9.0, 16.0, 25.0, 36.0, 49.0, 64.0, 81.0, 100.0, 121.0, 144.0, 169.0, 196.0, 225.0, 256.0, 289.0, 324.0, 361.0]
```

```bash
# 5. an image in, an image out -- the hello world, about a second
./voxlogica run --no-serve doc/gallery/programs/hello/hello-greyscale.imgql
```

That one must print

```
min=18.410999298095703
max=182.37600708007812
mean=85.00000127156575
```

and write `output/hello-greyscale/hello-greyscale.png`: the input's three colour
bars as three greys, 54, 182 and 18. It is the shortest program here that reads
an image, computes over it and writes a result -- section H says what to change
in it.

```bash
# 6. the measurement suite runs -- about 3 seconds, still no data
python3 tools/perf/suite.py --hold 0 --reps 1 --warm-runs 1 --experiments cache \\
    --program doc/gallery/programs/default/default-range-map.imgql \\
    --out perf/smoke --work-dir perf/work
python3 tools/perf/report.py perf/smoke
```

Three runs, then a two-row table whose `Problems` section reads `- none`. That
is the apparatus the paper's performance figures were taken with; section G
says how to point it at a real experiment.

Step 3 type-checks each program without computing; steps 4 and 5 compute, the
second on an image and back out to a file; step 6 measures.

If all six steps pass, the artifact is ready for the full evaluation. Experiment
1 is a good next move: it needs no dataset, about 7 minutes on four cores, and
peaks under 2 GB. Start it in the background and read section B.

## Resources

| | |
|---|---|
| Disk | 25 GB free (venv 8 GB, ~5 GB of results store per sweep; the artifact itself, image data included, is under 500 MB) |
| RAM, measured peak | at 4 threads: experiment 1: 2.1 GB; 2: 1.9 GB; 3: 2.4 GB; 4: 7.6 GB. At 24: experiment 2: 8.8 GB; 3: 7.8 GB. **16 GB runs all four**; 8 GB runs 1, 2 and 3 **if you pass `--threads 4`** -- the engine sizes itself by the cores it SEES, and a container with a memory cap still shows it all of them |
| CPU | any; the engine uses every core it finds. Each time in this README names the machine it was taken on |
| GPU | **experiment 4 only** (`nnunet_device = "cuda"`), and it needs **2.5 GB of GPU memory**, measured -- any CUDA card with about 3 GB free will do. Measured with driver 595.71.05; the shipped wheels are the CUDA 12.8 build (`torch 2.10.0`, `nvidia-cuda-runtime-cu12 12.8.90`). Without one, the device can be set to `"cpu"`: the program runs, but a test run of ours spent 90 minutes without finishing a single prediction, so we cannot say what that path costs |
| Network | once, during bootstrap; every download is version-pinned and hash-checked |
| OS | Linux x86-64 is what we measured on. `bootstrap.py` fetches its own interpreter, so the host Python does not matter |

On a small machine, experiments 1, 2 and 3 fit: measured in an Ubuntu 25.04
container with 4 cores, 8 GB and no GPU -- the six smoke-test steps in 2 min
20 s, then experiment 2 in 16 s, 3 in 1 min 24 s and 1 in 5 min 16 s.

Experiment 4 needs more: at `--threads 4`, the configuration section C gives,
it peaks at **7.6 GB**, so **16 GB is enough** and 8 GB leaves no margin. The
peak is the sweep's own working set -- twenty percentile-ranked volumes and
twenty-one candidate segmentations each -- so the ask is a 16 GB machine with a
CUDA device, not a large one.

---

## Running it in a container

A container image is published beside this archive, at the DOI above:
`voxlogica-tacas-image.tar.gz`, 5.0 GB compressed and 10.3 GB loaded. It carries
the engine, its pinned dependencies, the BraTS cases and the trained weights,
all installed when the image was built -- so nothing about your machine decides
what runs, and the container needs no network at all. `Dockerfile` in this
archive is what it was built from.

```bash
docker load -i voxlogica-tacas-image.tar.gz
mkdir -p out store
```

Then one command per experiment, each on one line. `out` collects the images the
programs write and `store` keeps the engine's results store on your disk, where
it survives the container and makes a second run instant:

```bash
# the one-second program, to see it work
docker run --rm -v "$PWD/out:/artifact/output" -v "$PWD/store:/root/.voxlogica" voxlogica-tacas:{image_tag} ./voxlogica run doc/gallery/programs/hello/hello-greyscale.imgql

# experiment 1 -- about 7 minutes on four cores
docker run --rm -v "$PWD/out:/artifact/output" -v "$PWD/store:/root/.voxlogica" voxlogica-tacas:{image_tag} ./voxlogica run doc/gallery/programs/nnunet/nnunet-circle-segmentation.imgql

# experiment 2 -- five cases
docker run --rm -v "$PWD/out:/artifact/output" -v "$PWD/store:/root/.voxlogica" voxlogica-tacas:{image_tag} ./voxlogica run doc/gallery/programs/brats2020/brats-five-cases.imgql

# experiment 3 -- the sweep
docker run --rm -v "$PWD/out:/artifact/output" -v "$PWD/store:/root/.voxlogica" voxlogica-tacas:{image_tag} ./voxlogica run doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql

# experiment 4 -- the one that needs the GPU
docker run --rm --gpus all -v "$PWD/out:/artifact/output" -v "$PWD/store:/root/.voxlogica" voxlogica-tacas:{image_tag} ./voxlogica run doc/gallery/programs/nnunet/brats-threshold-sweep-nnunet.imgql --threads 4
```

Four things to know:

1. **`--gpus all` needs the NVIDIA Container Toolkit** on the host, which is how
   Docker reaches a GPU at all. With podman the same thing is
   `--device nvidia.com/gpu=all`. Everything else here runs without a GPU.
2. **Experiment 2 writes its one file beside its program**, inside the
   container, not under `output/`. Leave out `--rm` and `docker cp` it out, or
   read the printed goals, which are what section C compares.
3. **podman takes these commands unchanged**, in place of `docker`.
4. **The rest of this README applies inside the container as it stands**: the
   expected values, the file counts, the sections on the store and on
   reproducibility. The working directory is `/artifact`, and
   `sha256sum -c SHA256SUMS` passes in there too.

The native path -- unpack this archive and run `./voxlogica` -- is the one the
rest of this README describes, and it is tested just as carefully. The image is
for a reviewer who would rather not have the host decide anything.

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
`.python-version` and creates `.venv` here: 137 packages, a minute or two
depending on the connection. It is idempotent. Its last line must read

```
Environment synchronized with the hashed lock files.
```

If it instead mentions `requirements.txt (NO LOCK FILE ...)`, the lock did not
travel with this archive and the resolved versions may differ from ours.
`implementation/python/requirements.lock` pins the whole transitive graph,
{n_locked} packages each with its sha256.

**Disk: have 25 GB free before you start.** The venv is 8 GB and each of the two
sweeps writes a ~5 GB results store. If space runs short the engine warns
`cache budget cut from ... by free space`.

Two requirements:

1. **Free-threaded CPython.** `.python-version` asks for `3.14t`; the engine
   refuses a stock build and says so.
2. **Always launch through `./voxlogica`.** It exports `PYTHON_GIL=0` before the
   interpreter starts and puts `.venv/bin` on `PATH`, where nnU-Net's
   executables live. `python -m voxlogica.main` appears to work and then fails
   inside nnU-Net with `No such file or directory:
   nnUNetv2_plan_and_preprocess`.

## B. The BraTS cases

They ship with the artifact. Nothing to download, nothing to edit.

| what | where | read by |
|---|---|---|
| the twenty scored cases, `BraTS20_Training_051` to `_070`, five volumes each | `data/MICCAI_BraTS2020_TrainingData/` | experiments 3 and 4 |
| `name_mapping.csv`, the metadata the population is selected from | the same directory | experiments 3 and 4 |
| the five-case sample | `doc/gallery/programs/brats2020/data/` | experiment 2 |

The twenty are HGG positions 50-69 of the official training set: both sweeps
keep the HGG rows of `name_mapping.csv` and slice, so the cases are chosen by
name. That file lists all 369, so those positions are positions in the full
dataset. The other 344 cases are not here; no shipped program names them, and
the ones that trained the network, positions 0-49, are not needed to use it
(section F).

**These are the official cases, unmodified.** To check that:

```bash
python3 tools/dataset_manifest.py data/MICCAI_BraTS2020_TrainingData --subset
```

Expected: `OK: the {n_dataset_files} files here are the ones the expected values
were measured on.` and exit status 0. It checks against
`tools/brats2020-manifest.json`, which pins all 369 cases of the official set by
their decompressed content.

To run the sweeps against your own copy of the full training set, point
`dataset_root` at it -- one line near the top of each of the two programs -- and
run the same check without `--subset`.

If you use these cases outside this evaluation, cite the challenge: Menze et
al., *IEEE TMI* 34(10), 2015; Bakas et al., *Nature Scientific Data* 4, 2017;
Bakas et al., arXiv:1811.02629, 2018.

## C. Run the experiments

{"".join(sections)}
---

## C.5 Reading experiments 3 and 4 together

Both score the same twenty cases -- HGG positions 50-69, `BraTS20_Training_051`
to `_070` -- with the same definitions of `preprocess`, `segment`, `sweep_of`,
`dice`, `vi_thresholds`, `published_vi_thr` and `whole_tumour`. The three
pairings are therefore comparable, and together they are the paper's claim:

| | where | mean | median |
|---|---|---|---|
| threshold sweep vs the **annotation** | exp 3, `dice_best_mean` | 0.8555 | 0.9074 |
| nnU-Net vs the **annotation** | exp 4, `model_dice_mean` | 0.8588 | 0.9152 |
| threshold sweep vs **nnU-Net** | exp 4, `dice_best_mean` | 0.8502 | 0.9259 |

The twenty cases are outside the network's training set, HGG positions 0-49.

**Nineteen goal names appear in both programs, and only `n_cases` means the same
thing in each**, because `truth_of` is the annotation in experiment 3 and the
prediction in experiment 4. So `dice_best_mean` is 0.8555 in one and 0.8502 in
the other, on the same cases: two quantities, not a discrepancy. The two
experiments also export a different ten worst cases.

**The exported PNGs are numbered by position, and the two programs count
differently:**

| | a file name | what it is |
|---|---|---|
| experiment 3 | `output/aiim-sweep/case_001_*` | position 1 of the twenty, `BraTS20_Training_052` |
| experiment 4 | `output/aiim-sweep-nnunet/case_053_*` | position 53 of the 293 HGG, `BraTS20_Training_054` |

`outlier_paths` prints the real path of every exported case, and that is the
mapping to use. Experiment 4 writes four images per plane instead of three:
`_flair`, `_predicted`, `_nnunet` and `_truth`.

---

## D. The store, and running twice

**A second run finishes instantly and writes no files.** Results are cached in
`~/.voxlogica`, keyed by content, so re-running an unchanged program returns the
stored values without recomputing and without re-writing the images. To compute
everything again, give the run a store of its own:

```bash
./voxlogica run <program> --store-db /path/to/a/new/store.db
```

That is how the numbers in section C were measured. The store is 5-7 GB for
either BraTS experiment and must not be on a tmpfs (so not under `/tmp` on most
systems).

**Do not run experiment 1 twice at the same time.** It puts its training tree in
a fixed directory under `/tmp`, so two concurrent runs corrupt each other's
preprocessing. Experiment 4 writes nothing outside `output/` and its store.

## E. Reproducibility

`--threads N` fixes the degree of parallelism, and the values do not depend on
it: experiment 3 returns the same digits at 1, 4 and 16 threads, warm store or
cold. A number that differs on your machine is not the scheduler.

The nnU-Net programs are the exception. Experiment 1 trains, so its output is
not bit-reproducible. Experiment 4 predicts with the shipped weights, which puts
it within 1e-4, with the winning thresholds identical every time.

## F. The trained model

{model_section}

## G. Measuring performance yourself

The paper's performance figures were taken with the suite that ships here. It
measures from outside the engine: wall clock, processor time and peak resident
memory come from `os.wait4`.

| file | what it does |
|---|---|
| `tools/perf/suite.py` | runs one program under a set of experiments, repeated and interleaved |
| `tools/perf/report.py` | one Markdown table per results directory, plus `summary.json` |
| `tools/perf/vl1_compare.py` | the same measurements for VoxLogicA 1 and VoxLogicA 2 on the sweep |

It needs only the standard library, so it runs under your system `python3`, and
it measures the `.venv` that section A built -- so do section A first.

```bash
python3 tools/perf/suite.py \
    --program doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql \
    --out perf/aiim --work-dir perf/work \
    --experiments scaling,cache --threads-list 1,4,8,24 --reps 3
python3 tools/perf/report.py perf/aiim
```

`--experiments` takes any of `scaling` (each width in `--threads-list`),
`fusion` (element-wise fusion on and off), `cache` (a cold run, then runs
against the store it left) and `resume` (a run interrupted with the signal
Ctrl-C sends, then restarted on that store).

**What the report flags.** Every run's goals are hashed, and runs of the same
program that disagree appear under `Problems` -- as do a non-zero exit, and a
run during which other processes took more than `--max-foreign` percent of a
core. On a busy machine, `--quiet-pct` and `--hold` make the suite wait for
quiet before each run.

`--work-dir` must not be on tmpfs. Each run gets a fresh store there, removed
afterwards unless you pass `--keep-stores`.

**Measuring a variant.** `--set NAME=VALUE` overrides a top-level definition
before the run. String values keep their quotes, and the shell must not eat
them:

```bash
--set 'dataset_root="data/MICCAI_BraTS2020_TrainingData"' --set case_count=2
```

The program as run is written to `program.imgql` beside the results. An
undefined name is an error.

**VoxLogicA 1.** `vl1_compare.py` needs a VoxLogicA 1 binary of your own (we
used 1.3.3). VoxLogicA 1 has no loops, so the script writes the sweep out for it
one case at a time, writes the VoxLogicA 2 programs from the same template, and
compares both tools' answers value by value before reporting any timing. Pass
`--first-case 51` for the twenty cases the experiments use.

Your numbers will not be ours; these were taken on 24 cores.

---

## H. Writing a program of your own

`doc/gallery/programs/hello/hello-greyscale.imgql` is the shortest program that
reads an image, computes something over it and writes a result. Its input ships
beside it -- 240x180, three colour bars.

```bash
./voxlogica run doc/gallery/programs/hello/hello-greyscale.imgql
```

Expected: exit status 0, three printed goals and one written file.

| | value | |
|---|---|---|
| `min` | 18.410999 | pure blue, 0.0722 x 255 |
| `max` | 182.37601 | pure green, 0.7152 x 255 |
| `mean` | 85.000001 | the three weights sum to 1 |
| `output/hello-greyscale/hello-greyscale.png` | 54, 182, 18 | the bars as three greys |

`intensity` is ITU-R BT.709 luminance, which is where those greys come from.

Four things to try on it:

1. **Delete the `save` goal.** The file is not written and the three numbers
   still print: a goal is a request for a value, and the engine computes what
   the goals need.
2. **Point `input_path` at another image.** Anything SimpleITK reads -- PNG,
   JPEG, NIfTI, DICOM -- in any dimension.
3. **Replace `intensity(colour)` with `green(colour)`.** `max` becomes 255.
4. **Add a goal:** `print "sigma" index(stats, 3)`. Nothing else recomputes.

To find out what else can be written:

```bash
./voxlogica list-primitives       # every primitive, by namespace, as JSON
./voxlogica typecheck <program>   # check a program without running it
```

`list-primitives` gives name, namespace, arity and the docstring of each
kernel. The kernels are under
`implementation/python/voxlogica/primitives/<namespace>/`, one package per
namespace; a new namespace is a directory with a `get_primitives()`, which is
how `vox1`, `simpleitk` and `nnunet` are built.

The four experiments are the other examples.
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
                    help="also write a .zip beside it (the AEC submission is a ZIP)")
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
