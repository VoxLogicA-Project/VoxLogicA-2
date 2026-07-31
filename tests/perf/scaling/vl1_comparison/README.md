# VL1 vs VL2 baseline comparison

Companion to [manuscripts/engine-scaling-2026-07.md](../../../../manuscripts/engine-scaling-2026-07.md)
Part III. Compares this engine (VL2) against the real historical VoxLogicA
binary (VL1) on the same reduced TACAS'19 recipe.

**Read Part III sec 14 before trusting any number from a run of this kit.**
A first attempt at this exact comparison reported VL2 as 5.54x faster than
VL1 and was wrong — VL1 was driven as 40 separate process launches, charging
it for ~34 seconds of pure dotnet startup overhead that has nothing to do
with the computation. The corrected number is 1.08x. The mistake passed every
internal check available at the time (real binary, real dataset, plausible
Dice, idle host) — only a second, structurally different measurement (or, as
actually happened, someone checking the methodology) caught it.

## Requirements

- VL1 itself: not part of this repo. Used at
  `/home/VoxLogicA/binaries/VoxLogicA_1.3.3-experimental_linux-x64/VoxLogicA`
  on fmt-5000. If you don't have access to a VL1 binary, this package cannot
  run its half of the comparison.
- The BraTS2020 training set, likewise not part of this repo (data-use
  agreement) — path defaults to
  `/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData` (fmt-5000 only).
- VL2 side: this repo's own `looping_experiment/test_speedup.py` (untracked,
  host-local — see its own docstring).

## Why one unrolled file, not N processes

VL1 has no `for`/`dir()` loop construct. The FAIR way to run N cases without
paying N process-startups is to concatenate N `load`/`let`/`print` blocks into
one file and submit it once — exactly how VL1's own real scripts already
sweep PARAMETERS (`/home/VoxLogicA/scripts/gen_GBM_multi.sh`); this generates
the same shape, sweeping cases instead. See `build_onefile.py`'s docstring for
two more gotchas found the hard way (VL1 identifiers can't contain
underscores; naive string substitution corrupts "pflair" and file paths via
substring collision).

## Running it

```bash
# 1. Generate the unrolled VL1 file (on the VL1 host, e.g. fmt-5000)
python3 build_onefile.py --cases 40 --out /tmp/vl1_40cases.imgql

# 2. Warm up (untimed) -- JIT/page-cache, matches VL2's own warmup convention
cd /home/VoxLogicA/binaries/VoxLogicA_1.3.3-experimental_linux-x64
./VoxLogicA /tmp/vl1_40cases.imgql > /tmp/warmup.log 2>&1

# 3. Timed run
time ./VoxLogicA /tmp/vl1_40cases.imgql > /tmp/timed.log 2>&1
grep -c dice_c /tmp/timed.log   # must equal --cases: confirms nothing failed silently

# 4. VL2 side, same case count, from the repo root's looping_experiment/
python3 ../../../../looping_experiment/test_speedup.py --cases 40
```

Compare the two wall-clock numbers directly; both are single-process,
single-host, same dataset slice (BraTS2020 cases 1..N).

## Known caveats (do not paper over these when reporting a number)

- **Threshold constants differ slightly** between VL1's proven recipe
  (hi=0.95/vi=0.86/correction=0.5, from the real historical spec) and VL2's
  `test_speedup.py` recipe (hi=0.93/vi=0.88/correction=0). Each side uses its
  own already-validated constants rather than forcing identical ones — Dice
  values are not meant to match case-by-case, only the *operator pattern and
  compute cost* are equivalent.
- **`test_speedup.py` requires `PYTHON_GIL=0`** to be set when invoking
  `.venv/bin/python` directly (it bypasses the `./voxlogica` wrapper that
  normally sets this). Without it, SimpleITK silently re-enables the GIL on
  import and you benchmark a GIL-enabled run without any indication in the
  output that happened.
- **The target `.venv` must actually be free-threaded** for a meaningful
  comparison — check `sysconfig.get_config_var("Py_GIL_DISABLED")` is `1`. A
  `.venv` that predates the free-threading cutover (commit `b4e170e`) needs
  `python3 bootstrap.py` re-run (needs `uv` on `PATH`) before this is valid.
- **Cross-check any new result against `doc/dev/free-threaded-handover.md`'s
  existing 8.17s VL1 figure** for this same recipe if reproducing this later —
  today's independent re-measurement landed at 8.157s, near-exact agreement,
  which is exactly what should be true of an unchanged historical binary and
  is the kind of check that would have caught the 5.54x error immediately.
