# Engine scaling study — reproducibility package

Companion to [manuscripts/engine-scaling-2026-07.md](../../../manuscripts/engine-scaling-2026-07.md).
Everything needed to regenerate that manuscript's measurements from a clean
checkout, without the BraTS2020 dataset (which is not part of this repo).

## Layout

- `generate_synthetic_cases.py` — deterministic, dataset-free volume generator
  (numpy + SimpleITK only). No RNG, no seed to record: same command always
  produces byte-identical output.
- `bench_scaling.imgql` — the workload. Same parameter grid, same operator
  graph shape (~26k nodes, work/span ≈ 274 — the "WIDE" class in
  `doc/dev/scaling-test-design.md` sec 6) as the real study's
  `_bench_scaling.imgql`, but reading the synthetic volumes above instead of
  BraTS. Verified to produce 26,477 nodes vs the original's 26,297 (0.7% drift,
  from synthetic masks taking slightly different `maxvol`/`fill_holes` paths —
  expected, does not affect the performance characteristics under study).
- `run_scaling_suite.sh` — the full measurement harness: idle gate, P-core-only
  sweep, E-core-only sweep, hybrid sweep, topdown attribution, `perf record`
  symbol attribution. This is the actual script used to produce the
  manuscript's tables (adapted from the ad hoc versions used during the
  investigation — see `git log` on this file for what changed and why).
- `analyze_results.py` — turns `run_scaling_suite.sh`'s raw output into the
  manuscript's tables (speedup, efficiency, cores-busy, IPC).

## What this package can and cannot reproduce

**Can:** the engine's scheduling behavior, achieved concurrency (`saturation`),
CPU-time inflation with worker count, the P-core/E-core throughput asymmetry,
and topdown stall attribution — all properties of the *engine and the
hardware*, not of the specific images.

**Cannot:** the real study's absolute Dice numbers or anything about
segmentation quality — this dataset is synthetic Gaussian blobs, not brain
scans. Do not cite `avg_oracle_best` from this workload as a clinical result;
see [[brats-segmentation-findings]] for that (separate, dataset-dependent) line
of work.

## Quick start

```bash
cd tests/perf/scaling
PYTHONPATH=../../../implementation/python \
  ../../../.venv/bin/python generate_synthetic_cases.py _synthetic_data

PYTHONPATH=../../../implementation/python PYTHON_GIL=0 \
  ../../../.venv/bin/python -m voxlogica.main run bench_scaling.imgql \
  --no-cache --threads 4 --store-db /tmp/scaling.sqlite
```

Look for `"saturation"` and `"mean_concurrency"` in the printed JSON — those
are the achieved-concurrency numbers from `engine/concurrency_probe.py`
(sec 1-2 of the manuscript). Wall-clock alone is NOT sufficient evidence; see
`doc/dev/scaling-test-design.md` sec 0-1 for why, in detail — four
conclusions in one session were reversed by ignoring that rule, and it is not
worth relearning.

## Full measurement protocol

```bash
./run_scaling_suite.sh /tmp/scaling-results
python3 analyze_results.py /tmp/scaling-results
```

Requires Linux + `perf` + a hybrid P/E CPU for the P-core/E-core/topdown
sections; the basic wall-clock/saturation sweep runs anywhere. See the script's
own header comment for what each stage measures and why, and
`doc/dev/scaling-test-design.md` sec 5 for the non-negotiable protocol rules
(idle gate, interleaving, min-of-N, genuinely serial baseline) it implements.

**Machine-specific numbers do not transfer.** The manuscript's P-core/E-core
ratio (0.755x measured, see sec 4b) and the ITK thread-count crossover
(sec 4-5) are properties of the exact CPU (`Intel Ultra 9 285K`, 8P+16E) the
study ran on. Re-run this suite on your own hardware before trusting any
number from the manuscript as a general claim rather than as "what fmt-5000
does."

## Provenance

The real study's dataset-dependent files (`experiment.imgql`, `utils.imgql`,
`_bench_scaling.imgql`) live in `looping_experiment/`, which is git-excluded
(`.git/info/exclude`) by design — it is host-local research state, not part of
this repo's history. `bench_scaling.imgql` in this directory copies the
relevant definitions verbatim (see its header comment for exactly which lines
and from where) so this package has no dependency on that directory.
