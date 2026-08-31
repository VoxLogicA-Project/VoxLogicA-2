# BraTS2020 tests — setup and running

An operating guide to the ImgQL programs that run on the BraTS2020 dataset.
Every section stands on its own: follow §1 the first time, and after that use only
§5 (the table of commands).

Conventions used throughout this document:

| symbol | meaning |
|---|---|
| `$` | a line to paste into the terminal |
| ✅ | what you should see if it worked |
| ❌ | what you see if it went wrong → go to §10 |
| ⚠️ | surprising but correct behaviour, not a fault |

**Rule number one: every command is run from the repository root**
(`/home/laura/VoxLogicA-2`). The paths inside the programs are relative to it.

## Contents

| § | contents | when you need it |
|---|---|---|
| [1](#1-prerequisites) | Prerequisites | first time |
| [2](#2-setup--done-once) | Setup | first time |
| [3](#3-map-of-the-programs) | Map of the programs | to get your bearings |
| [4](#4-the-shape-of-the-command) | How a program is launched | first time |
| [5](#5-the-commands-one-program-at-a-time) | **The commands, one by one, with expected results** | every time |
| [6](#6-smoke-test--the-order-to-try-them-in) | Smoke test | after every change |
| [7](#7-why-two-programs-compute-nothing) | Laziness | when a program "passes" without doing anything |
| [8](#8-the-knobs-what-to-change-and-where) | Parameters | to run experiments |
| [9](#9-files-produced-and-cleanup) | Output and cleanup | after a session of runs |
| [10](#10-errors-symptom--cause--fix) | Errors | when something breaks |
| [11](#11-these-are-not-pytest-tests) | Why these are not pytest tests | worth knowing once |
| [12](#12-quick-reference) | Quick reference | once setup is done |

---

## 1. Prerequisites

Three things, to be checked one at a time. Each has its own verification command.

### 1.1 — You are in the right directory

```bash
$ cd /home/laura/VoxLogicA-2
$ pwd
```

✅ `/home/laura/VoxLogicA-2`

### 1.2 — Python 3 is installed

```bash
$ python3 --version
```

✅ any version ≥ 3.9 (it is only needed to launch `bootstrap.py`; the real interpreter
is installed by that script, see §2.2).

### 1.3 — The BraTS2020 dataset is present and readable

```bash
$ ls /home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData | head -3
$ ls /home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData | wc -l
```

✅ the first three lines are `BraTS20_Training_001`, `_002`, `_003`, and the count is **372**
(= 369 cases + `dataset.json` + `name_mapping.csv` + `survival_info.csv`).

❌ `Permission denied` or `No such file or directory` → the directory belongs to
`vincenzo`, mode `drwxrwxr-x`. If it has disappeared there is nothing to fix on the
repository side: it is a machine-access problem.

**Dataset layout**, for reference (one folder per case, five files inside):

```
MICCAI_BraTS2020_TrainingData/
├── BraTS20_Training_001/
│   ├── BraTS20_Training_001_flair.nii.gz   ← the image that gets segmented
│   ├── BraTS20_Training_001_t1.nii.gz
│   ├── BraTS20_Training_001_t1ce.nii.gz
│   ├── BraTS20_Training_001_t2.nii.gz
│   └── BraTS20_Training_001_seg.nii.gz     ← the ground truth
├── BraTS20_Training_002/
└── … up to 369
```

Geometry of every volume: **240 × 240 × 155 = 8,928,000 voxels**.
(The number 8928000 shows up in the sweep output: it is the quickest way to confirm
at a glance that the data really was read.)

---

## 2. Setup — done once

### 2.1 — Go to the repository

```bash
$ cd /home/laura/VoxLogicA-2
```

### 2.2 — Build the environment

```bash
$ python3 bootstrap.py
```

What it does, in order:

1. looks for `uv` on `PATH`; if it is missing, downloads a checksum-verified copy into
   `.cache/uv/bin` (no `sudo` needed, nothing written outside the checkout);
2. reads `.python-version` (which holds **`3.14t`** — the `t` means *free-threaded*);
3. creates or updates `.venv/` with that interpreter and the pinned dependencies,
   `pytest` included.

✅ finishes without errors; afterwards `.venv/bin/python` exists.

⚠️ The first run can take a few minutes (it downloads the interpreter). Later runs are
nearly instant: `bootstrap.py` is re-invoked automatically by `./voxlogica` on every
execution, and it is idempotent.

**Why a special Python:** the engine requires the GIL-free interpreter. The `./voxlogica`
script checks this and, if `.venv` is not free-threaded, stops with an explicit error
instead of silently running serialized.

### 2.3 — Check that the command responds

```bash
$ ./voxlogica version
```

✅ prints a version number.

❌ `ERROR: … is not a free-threaded build.` → §10, row 2.

### 2.4 — Only for the five-case gallery program

`brats-five-cases.imgql` does not read from the full dataset: it expects five cases in
`doc/gallery/programs/brats2020/data/`. That folder is **not in git** (it is 40 MB, and
it is excluded by `doc/gallery/programs/brats2020/.gitignore`).

Rebuild it with **symbolic links** — no copying, no disk space used:

```bash
$ SRC=/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData
$ DST=/home/laura/VoxLogicA-2/doc/gallery/programs/brats2020/data
$ for n in 002 079 089 230 328; do
    C=BraTS20_Training_$n
    mkdir -p "$DST/$C"
    for m in flair t1 t1ce t2 seg; do
      ln -sf "$SRC/$C/${C}_$m.nii.gz" "$DST/$C/${C}_$m.nii.gz"
    done
  done
```

Verification:

```bash
$ ls doc/gallery/programs/brats2020/data
$ git status --short doc/gallery/programs/brats2020/data
```

✅ the first command lists five folders; the second **prints nothing**
(which means it is ignored by git, as it should be).

> Note: the gallery README documents a rebuild using `gzip -c` from uncompressed `.nii`
> files. On our dataset the files are **already** `.nii.gz`, so the correct recipe is
> the `ln -sf` one above.

---

## 3. Map of the programs

Six programs. The **prints?** column is the important one: two of them, exactly as they
stand in the repository, exit successfully without computing anything (explained in §7).

The times are **cold-cache**, i.e. the first run. Re-running the same program right
afterwards drops to 1–2 seconds, because the results are already cached (§4.4).

| # | file | what it does | cases | time | prints? |
|---|---|---|---|---|---|
| 1 | `tests/brats_brain_tumour_segmentation.imgql` | fixed-threshold segmentation (0.93 / 0.88) | 15 | ~5 s | ✅ yes |
| 2 | `tests/threshold_sweep.imgql` | sweep of 100 thresholds per case | 100 | ~18 s | ✅ yes |
| 3 | `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql` | the full AIIM study: sweep + Dice + statistics + export | 20 | ~27 s | ✅ yes |
| 4 | `doc/gallery/programs/brats2020/brats-five-cases.imgql` | the gallery's five sample cases | 5 | ~5 s | ✅ yes |
| 5 | `tests/brats_flair_mean_threshold.imgql` | symbolic sweep, 9 thresholds | 10 | 0.14 s | ❌ **no** |
| 6 | `tests/brats_best_axial_slice.imgql` | exports the best axial slice as PNG | 3 | 0.14 s | ❌ **no** |

**Not ported to BraTS2020** (they still point at BraTS 2019 paths, two of which no
longer exist): `tests/brats_segmentation_with_for.imgql`,
`doc/gallery/programs/simpleitk/sitk-brats-fixed-segmentation.imgql`,
`doc/gallery/programs/simpleitk/sitk-threshold-sweep-overlay.imgql`.
If you need them, changing `dataset_root` is all it takes, as in the others.

---

## 4. The shape of the command

Always the same:

```bash
$ ./voxlogica run <path/to/program.imgql>
```

Four precautions, one per subsection.

### 4.1 — Do not pipe the output into `tail` or `head`

The engine also prints JSON telemetry: if you truncate, the `result=` line disappears.
To keep the output, redirect everything to a file and search inside it:

```bash
$ ./voxlogica run tests/threshold_sweep.imgql > /tmp/sweep.log 2>&1
$ grep '^result=' /tmp/sweep.log
```

### 4.2 — Time the run

This is how you notice false positives: a program that finishes in 0.14 s has computed
nothing (§7).

```bash
$ time ./voxlogica run <file>
```

### 4.3 — Check the exit code

The absence of red on screen is not enough.

```bash
$ echo $?
```

✅ `0`

### 4.4 — The results cache

The engine keeps every computed result in `~/.voxlogica/results.db` (plus the payload
files in `~/.voxlogica/results.db.files/`). It is already ~40 MB.

The practical consequence, which is confusing at first:

| run | AIIM | sweep |
|---|---|---|
| the first one (cold cache) | ~27 s | ~18 s |
| the following ones (warm cache) | ~2 s | ~1.5 s |

⚠️ **A time of 2 s does not mean the program did nothing.** Telling this case apart from
the one in §7 is easy: here the output is present and complete, there it is missing
altogether.

If you want to measure the real computation time, or you suspect the cache is handing
you a stale result after you changed something:

```bash
$ ./voxlogica run --no-cache <file>      # recompute without reading or writing the store
```

`--no-cache` is the safe choice when in doubt: it deletes nothing. There is also
`--delete-cache`, which **removes** the database and the payloads (it asks for
confirmation) — needed only if the cache has become corrupted.

> In reality the cache is keyed on the content of the expressions, so changing a
> parameter produces new nodes and recomputes on its own. `--no-cache` is for measuring,
> not for fixing.

---

## 5. The commands, one program at a time

### 5.1 — Segmentation (15 cases)

```bash
$ ./voxlogica run tests/brats_brain_tumour_segmentation.imgql
```

- ✅ exit `0`, about **5 s**
- ✅ one `result=[…]` line with **15 numbers** (the tumour volumes in voxels):
  ```
  result=[155959.0, 85676.0, 56765.0, 110433.0, 12284.0, 136688.0, 47241.0,
          150691.0, 128123.0, 37092.0, 74934.0, 45471.0, 50445.0, 105257.0, 134382.0]
  ```
- writes nothing to disk

### 5.2 — Threshold sweep (100 cases × 100 thresholds)

```bash
$ ./voxlogica run tests/threshold_sweep.imgql > /tmp/sweep.log 2>&1 ; echo $?
```

- ✅ exit `0`, about **18 s**
- ✅ `result=` is a list of **100 lists** of 100 numbers each
- ✅ the first number of every row is `8928000.0` → the volumes really were read

Checking the number of cases:

```bash
$ grep -o '], \[' /tmp/sweep.log | wc -l
```
✅ `99` (99 separators = 100 lists)

⚠️ If you get **4** instead of 99, you are running the old, lazy version of the file:
see §7.1.

### 5.3 — AIIM study (20 cases, 21 thresholds)

```bash
$ ./voxlogica run doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql
```

- ✅ exit `0`, about **27 s**
- ✅ writes **90 PNGs** into `output/aiim-sweep/` (10 outlier cases × 3 planes × 3 images)
- ✅ prints these statistics:

| key | expected value |
|---|---|
| `n_cases` | 20 |
| `dice_fixed_mean` (vi = 0.88, the published value) | 0.8069 |
| `dice_best_mean` | 0.8514 |
| `dice_best_median` | 0.8905 |
| `dice_best_stdev` | 0.1094 |
| `vi_thr_mean` / `median` / `stdev` | 0.89 / 0.91 / 0.0388 |
| worst case | index 7 (`BraTS20_Training_008`), Dice 0.458 |

Checking the PNGs:

```bash
$ ls output/aiim-sweep/*.png | wc -l
```
✅ `90`

⚠️ **Known limitation:** the sweep runs from 0.72 to 0.92, and the value 0.92 — the top
of the range — wins in 10 cases out of 20. The true optimum probably lies higher, so
`dice_best_mean = 0.8514` is a lower bound. To check that, widen the range: see §8.

### 5.4 — The five gallery cases

Requires step §2.4 to have been done.

```bash
$ ./voxlogica run doc/gallery/programs/brats2020/brats-five-cases.imgql
```

- ✅ exit `0`, about **5 s**
- ✅ writes `case_230_segmentation.nii.gz` **into the repository root** (see §9)
- ✅ the numbers match the table in the gallery README:

| case | gallery README | current run |
|---|---|---|
| 002 | 0.896 | 0.8959 |
| 079 | 0.000 | 0.0 |
| 089 | 0.652 | 0.6519 |
| 230 | 0.958 | 0.9585 |
| 328 | 0.913 | 0.9131 |
| **average** | **0.684** | **0.6839** |

⚠️ Case 079 scoring **0.000** is correct and deliberate: it is the smallest tumour in
the dataset, it raises no seed at all, and so there is nothing to grow from. It is in
the sample precisely to show where the method stops applying.

### 5.5 and 5.6 — The two programs that print nothing

```bash
$ ./voxlogica run tests/brats_flair_mean_threshold.imgql   # exits 0 in 0.14 s
$ ./voxlogica run tests/brats_best_axial_slice.imgql       # exits 0 in 0.14 s
```

They exit successfully **without computing anything**. This is not a fault: see §7.

---

## 6. Smoke test — the order to try them in

Shortest to longest. If one fails, stop there: the later ones will fail for the same
reason.

| order | command | time | done |
|---|---|---|---|
| 1 | `./voxlogica version` | <1 s | ☐ |
| 2 | `./voxlogica run doc/gallery/programs/brats2020/brats-five-cases.imgql` | ~5 s | ☐ |
| 3 | `./voxlogica run tests/brats_brain_tumour_segmentation.imgql` | ~5 s | ☐ |
| 4 | `./voxlogica run tests/threshold_sweep.imgql` | ~18 s | ☐ |
| 5 | `./voxlogica run doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql` | ~27 s | ☐ |

Total: **a little over one minute** — which puts the batch over the 30-second
threshold, so it follows the rule in §6.1 rather than blocking the terminal.

### 6.1 — Anything longer than 30 seconds

Repository rule, from `AGENTS.md` ("Long-Running Commands"): a command expected to take
more than 30 seconds must **write to a log file** and be launched with a **watch command
you can paste elsewhere**. Never block silently — a foreground run gives you no
visibility into how far it has got.

The pattern, applied to the whole smoke test:

```bash
$ mkdir -p /tmp/brats-smoke
$ ( for f in doc/gallery/programs/brats2020/brats-five-cases.imgql \
             tests/brats_brain_tumour_segmentation.imgql \
             tests/threshold_sweep.imgql \
             doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql; do
      echo "=== $f"
      /usr/bin/time -f "  %e s  exit=%x" ./voxlogica run "$f"
    done ) > /tmp/brats-smoke/all.log 2>&1 &
```

Watch it live, in this terminal or another one:

```bash
$ tail -f /tmp/brats-smoke/all.log
```

Read the verdict when it is done — four `=== ` banners, and four `exit=0`:

```bash
$ grep -E '^(=== |  [0-9])' /tmp/brats-smoke/all.log
```

✅ every line ends in `exit=0`

⚠️ `/usr/bin/time`, with the full path, not the shell builtin `time` — only the binary
takes `-f`. `%x` is the exit status of the program it timed, so a failure shows up in
the log instead of scrolling past.

---

## 7. Why two programs compute nothing

VoxLogicA-2 is **demand-driven**: it computes only what a goal (`print` or `save`) asks
for. A top-level definition that nobody prints produces no work at all — the engine sees
it, reduces it, and never executes it.

Consequence: a program with no `print` and no `save` **always exits 0**, in 0.14 s,
whatever is written inside it. You cannot tell a correct program from a broken one until
you ask it for a result.

That is exactly how the missing-import bug in `brats_flair_mean_threshold.imgql` was
found (§10, row 1): the file had been broken all along, but it "passed".

### 7.1 — How the laziness was removed from the sweep

`tests/threshold_sweep.imgql` used to print only the first 5 cases:

```diff
- dataset_small_slice_stop = 5
- print "result" subsequence(result, dataset_slice_start, dataset_small_slice_stop)
+ print "result" result
```

Effect: from 1.14 s (5 cases) to 17.88 s (100 cases).

### 7.2 — How to make the other two compute, without touching the repository

General recipe: **copy the file elsewhere and append a `print`.**

For `brats_flair_mean_threshold.imgql`:

```bash
$ cp tests/brats_flair_mean_threshold.imgql /tmp/probe1.imgql
$ echo 'print "sweep_volumes" for m in index(vi_sweep_masks,0) do volume(m)' >> /tmp/probe1.imgql
$ ./voxlogica run /tmp/probe1.imgql
```

✅ ~2 s, nine volumes in decreasing order:
```
sweep_volumes=[192928.0, 187979.0, 182796.0, 175445.0, 166385.0,
               155959.0, 143372.0, 127771.0, 111029.0]
```

⚠️ The sixth value, **155959.0**, is identical to the first value in §5.1. It is the
same threshold (vi = 0.88) on the same case: the two programs agree. It is the fastest
cross-check available.

For `brats_best_axial_slice.imgql`:

```bash
$ cp tests/brats_best_axial_slice.imgql /tmp/probe2.imgql
$ echo 'print "saved" saved' >> /tmp/probe2.imgql
$ ./voxlogica run /tmp/probe2.imgql
$ ls tests/output/brats-tmp-slices/
```

✅ ~3 s, `saved=[None, None, None]` and three PNGs:
`case_0_best_slice.png`, `case_1_best_slice.png`, `case_2_best_slice.png`.

⚠️ `None` is normal: `WriteImage` is an effect, it does not return a value. The proof
that it worked is the PNGs, not the list.

If you decide those `print`s should become permanent, add them to the real files — the
line to add is the same one.

---

## 8. The knobs: what to change and where

| I want to… | file | line to touch |
|---|---|---|
| use a different dataset | all of them | `dataset_root = "…"` |
| more or fewer cases | `brats_brain_tumour_segmentation` | `dataset_slice_stop = 15` |
| | `threshold_sweep` | `dataset_slice_stop = 100` |
| | `brats-threshold-sweep-aiim` | `case_count = 20` |
| | `brats_flair_mean_threshold` | `k = 10` |
| | `brats_best_axial_slice` | `dataset_slice_stop = 3` |
| change the fixed thresholds | segmentation, slice, AIIM | `hi_thr = 0.93`, `vi_thr = 0.88` |
| **widen the AIIM sweep past 0.92** | `brats-threshold-sweep-aiim` | `map(tick_to_threshold, range(72, 93))` → e.g. `range(72, 99)` |
| more or fewer exported outliers | `brats-threshold-sweep-aiim` | `outlier_count = 10` |
| change where the PNGs land | `brats-threshold-sweep-aiim` | `output_dir = "output/aiim-sweep"` |
| | `brats_best_axial_slice` | `output_dir = "tests/output/brats-tmp-slices"` |

⚠️ **Widening the sweep or raising `case_count` pushes the run past 30 seconds.**
At that point use the log-and-watch pattern of §6.1, not a foreground run — it is a
repository rule (`AGENTS.md`), not a preference. Keep it a single `voxlogica run`
invocation: splitting a workload across processes is forbidden by `AGENT.md` §6, and a
run that stalls or exhausts memory is an engine bug to report, not something to route
around by chunking it.

⚠️ **`z_planes` in `brats_best_axial_slice.imgql` must be 155**, not 192. BraTS2020 has
155 axial planes; the value 192 (inherited from BraTS 2019) made the loop run over
planes that do not exist — harmless but wasted.

⚠️ **There is never any need to hoist a repeated subexpression by hand.** Equal
expressions are the same graph node, whatever scope they are written in: the engine
computes them once. This is stated in the header comment of the AIIM program.

---

## 9. Files produced, and cleanup

| path | produced by | in git? |
|---|---|---|
| `output/aiim-sweep/*.png` (90 files) | AIIM | no, `output/**` is ignored |
| `tests/output/brats-tmp-slices/*.png` | axial slice | no, `tests/output/` is ignored |
| `doc/gallery/programs/brats2020/data/` | your `ln -sf` from §2.4 | no, local `.gitignore` |
| `case_230_segmentation.nii.gz` (root) | five cases | ⚠️ **yes, it shows up as untracked** |

The last one is the only thing that dirties `git status`: the `save` in the program uses
a relative path, which resolves against the current directory. Delete it after a run:

```bash
$ rm -f case_230_segmentation.nii.gz
```

Full cleanup of the outputs:

```bash
$ rm -rf output/aiim-sweep tests/output/brats-tmp-slices case_230_segmentation.nii.gz
```

> Project constraint, from `INCOMING-LOG.md`: partial analyses of the study must not be
> committed. All the outputs above are already ignored except the last one.

---

## 10. Errors: symptom → cause → fix

| # | symptom | cause | fix |
|---|---|---|---|
| 1 | `StaticAnalysisError: Unbound variable 'touch'` | `import "vox1"` is missing | add `import "vox1"` below `import "simpleitk"`. `vox1` supplies `touch`, `smoothen`, `geq_sv`, `leq_sv`, `percentiles` |
| 2 | `ERROR: … is not a free-threaded build.` | `.venv` was built with a GIL Python | `rm -rf .venv && python3 bootstrap.py` |
| 3 | exits `0` in 0.14 s, no output, no files | no `print`/`save`: laziness | §7 |
| 4 | the `result=` line is missing | you piped the output into `tail`/`head` | redirect to a file (§4.1) |
| 5 | `result=[]`, or fewer cases than expected | wrong `dataset_root`, or `dir()` finds no files | re-check §1.3; the pattern searched for is `*_flair.nii.gz` with recursion on |
| 6 | `dir()` does not find the five gallery cases | §2.4 not done, or the links are broken | `ls -l doc/gallery/programs/brats2020/data/BraTS20_Training_002/` — the arrows must point at files that exist |
| 7 | `Unbound variable 'format_string'` or `'concat'` | `import "strings"` is missing | add it |
| 8 | a case scores Dice `0.0` | may well be correct (case 079, §5.4) | compare against the expected table before treating it as a fault |
| 9 | the program finishes in 2 s instead of the expected 27, but the output is complete | warm cache, not an error | §4.4; to measure the real time use `--no-cache` |

---

## 11. These are NOT pytest tests

An important distinction, because the names are misleading.

| | Python suite | BraTS programs |
|---|---|---|
| where | `tests/unit/`, `tests/contract/`, `tests/integration/`, `tests/e2e/`, `tests/regression/`, `tests/perf/` | `tests/*.imgql`, `doc/gallery/programs/**/*.imgql` |
| how they are run | `./tests/run-tests.sh` | `./voxlogica run <file>` |
| collected by pytest | ✅ yes (`python_files = test_*.py`) | ❌ **no** |
| touch the dataset | no | yes |

`pytest.ini` collects only files named `test_*.py`. The `.imgql` files are collected by
no suite at all: **there is no automated test** covering BraTS segmentation or the
sweep. They are scripts run by hand, and the expected values are the ones in §5 of this
document.

To run the actual Python suite (a different thing; it does not touch BraTS):

```bash
$ ./tests/run-tests.sh
```

---

## 12. Quick reference

To paste without thinking, once setup is done:

```bash
cd /home/laura/VoxLogicA-2

# segmentation, 15 cases, ~5 s
./voxlogica run tests/brats_brain_tumour_segmentation.imgql

# sweep, 100 cases, ~18 s
./voxlogica run tests/threshold_sweep.imgql > /tmp/sweep.log 2>&1
grep -o '], \[' /tmp/sweep.log | wc -l      # must print 99

# AIIM study, 20 cases, ~27 s, 90 PNGs
./voxlogica run doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql

# five gallery cases, ~5 s
./voxlogica run doc/gallery/programs/brats2020/brats-five-cases.imgql

# cleanup
rm -f case_230_segmentation.nii.gz
```
