# Replicating the TACAS'19 and AIIM experiments

A briefing for someone picking this up cold. Everything named here exists in
the repository today; the datasets do not, and where to find them is said
below.

## What already exists, and what each thing is

| experiment | file | language | what it is |
|---|---|---|---|
| TACAS'19 recipe | `tests/perf/scaling/vl1_comparison/reduced_recipe.imgql.template` | **VoxLogicA 1** | The reduced TACAS'19 whole-tumour recipe, as a template with `$INPUTDIR`/`$NAME` placeholders. `build_onefile.py` beside it unrolls the template over N cases into one file, because VL1 has no loop construct. |
| the same recipe, in this engine | `doc/gallery/programs/simpleitk/sitk-brats-fixed-segmentation.imgql` | VoxLogicA 2 | The published recipe at fixed thresholds, over a slice of cases. |
| the same recipe, as a five-case sampler | `doc/gallery/programs/brats2020/brats-five-cases.imgql` | VoxLogicA 2 | Same method, five cases, ~5 s, data carried beside the program. |
| AIIM study | `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql` | VoxLogicA 2 | The full study as one declarative program: the threshold sweep, per-case best threshold, the statistics over the chosen thresholds, and the export of the worst cases. |

So: **no new `.imgql` has to be written to replicate either experiment.** The
work is running them, checking the numbers, and recording what came out.

## The method both experiments share

FLAIR only, no training. Background is what touches the image border below an
intensity of 0.1; the brain is its complement; `percentiles` replaces each voxel
by its rank within the brain, which is what removes the need to normalise
intensities across patients. Then two thresholds on that percentile image: a
strict one seeds the tumour, a permissive one bounds how far the seed may grow,
and `grow` (reachability) fills the seed out to the permissive region.

**The two implementations use different constants, deliberately.** VL1's proven
recipe is `hi=0.95`, `vi=0.86`, correction radius `0.5`. The VoxLogicA-2 gallery
programs use `hi=0.93`, `vi=0.88`, correction `0`. Each side keeps the constants
that were validated for it. Dice values are therefore not expected to match
case-by-case between the two engines — only the operator pattern is equivalent.
Do not "fix" this by forcing one set of constants onto both without saying so.

## Data

Neither dataset is in git (data-use agreement). On **fmt-5000**:

- `/home/VoxLogicA/datasets/BraTS_2019_HGG` — 259 cases, one folder each. The
  repository's `tests/data/datasets/BraTS_2019_HGG` is a symlink to it, which is
  the path the gallery programs expect.
- `/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData` — used by the VL1
  comparison kit.

On any other machine, `tests/data/datasets/BraTS_2019_HGG` has to be created as
a symlink to a local copy with the same layout, or the programs will find no
files and print empty sequences rather than failing loudly.

## Running the AIIM study

```bash
./voxlogica run doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql
```

It sweeps the permissive threshold over `0.72 … 0.92` in steps of `0.01` with
the strict threshold fixed at `0.93`, on the first 20 cases, and prints:

- `dice_fixed_mean` — the published single threshold, scored;
- `dice_best_mean`, `dice_best_median`, `dice_best_stdev` — the per-case best;
- `vi_thr_mean`, `vi_thr_median`, `vi_thr_stdev`, `vi_thr_distribution` — which
  thresholds won, and how often;
- `outlier_cases`, `outlier_dice`, `outlier_paths` — the ten worst;
- `exported_planes`, `exported` — three planes per worst case, written under
  `output/aiim-sweep`.

The gap between `dice_fixed_mean` and `dice_best_mean` is the point of the
study: it is how much the single published threshold costs, and it is an upper
bound obtained by consulting the ground truth, not a method. Say so whenever
either number is quoted.

`case_count` and `outlier_count` are the first two constants in the file. Raise
`case_count` to run the full 259.

## Running the TACAS'19 comparison against VoxLogicA 1

**Read `tests/perf/scaling/vl1_comparison/README.md` and Part III §14 of
`manuscripts/engine-scaling-2026-07.md` before trusting any timing from this.**
The first attempt at this comparison reported VoxLogicA 2 as 5.54x faster and
was wrong: VL1 had been driven as 40 separate process launches, charging it
~34 s of pure dotnet startup. The corrected figure is 1.08x. The error passed
every check available at the time. Do not report a speedup from this kit
without an independent second measurement.

```bash
# 1. unroll the template into one VL1 file (on fmt-5000)
cd tests/perf/scaling/vl1_comparison
python3 build_onefile.py --cases 40 --out /tmp/vl1_40cases.imgql

# 2. warm up, untimed
cd /home/VoxLogicA/binaries/VoxLogicA_1.3.3-experimental_linux-x64
./VoxLogicA /tmp/vl1_40cases.imgql > /tmp/warmup.log 2>&1

# 3. timed run
time ./VoxLogicA /tmp/vl1_40cases.imgql > /tmp/timed.log 2>&1
grep -c dice_c /tmp/timed.log      # must equal --cases, or something failed quietly

# 4. this engine, same case count
python3 looping_experiment/test_speedup.py --cases 40
```

Three things that silently invalidate the comparison:

- `test_speedup.py` bypasses the `./voxlogica` wrapper, so `PYTHON_GIL=0` must
  be set when invoking `.venv/bin/python` directly. Without it SimpleITK
  re-enables the GIL on import and the run is measured with the GIL on, with
  nothing in the output to say so.
- The `.venv` must actually be free-threaded: check
  `sysconfig.get_config_var("Py_GIL_DISABLED") == 1`. An older venv needs
  `python3 bootstrap.py` (requires `uv` on `PATH`).
- Cross-check any VL1 number against the 8.17 s figure recorded in
  `doc/dev/free-threaded-handover.md` for this same recipe. An unchanged
  historical binary should reproduce it closely; a re-measurement landed at
  8.157 s. A large disagreement means the setup is wrong, not that the binary
  changed.

## What to record

Every run's output goes into the repository, including runs that failed. Put
them under `looping_experiment/results/` with a short README saying, for each
number: what it was measured against, what the inputs were, and by what method
it was produced. A number without those three is not reportable.
