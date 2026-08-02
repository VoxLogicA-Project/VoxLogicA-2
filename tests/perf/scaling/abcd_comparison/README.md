# A/B/C/D comparison: VL1, `main`, `incoming`, plain Python

Companion to [manuscripts/engine-scaling-2026-07.md](../../../../manuscripts/engine-scaling-2026-07.md)
Part V, the concluding comparison of this investigation. Four independent
implementations of the identical reduced TACAS'19 recipe (threshold + grow,
no cross-correlation), same 40 BraTS2020 cases, same host (fmt-5000).

| arm | what it is | orchestration |
|---|---|---|
| A | VL1 (real historical binary) | Hopac-based task parallelism (VL1's own) |
| B | `main` branch (pre-engine VL2) | single-threaded lazy evaluator |
| C | `incoming` branch (this engine) | calibrated async scheduler + fusion |
| D | `plain_python_reference.py` (this dir) | **none** — straight-line sequential Python |

Arm D exists to answer a question A-C cannot: how much of the engine's
wall-clock is the orchestration layer earning its keep, versus what any
direct implementation gets for free just by calling ITK filters in an
obvious order? It imports exactly two VL2 kernels rather than reimplementing
them (`percentiles`, `through` — both have real algorithmic content beyond a
single ITK filter call); everything else is one direct SimpleITK call.

## Requirements

Same as [../vl1_comparison/README.md](../vl1_comparison/README.md): VL1
binary and the BraTS2020 dataset, neither part of this repo. Arm B
additionally needs a `main`-branch worktree with its own bootstrapped
`.venv` (`git worktree add /tmp/vl2_main_baseline origin/main && cd
/tmp/vl2_main_baseline && python3 bootstrap.py`).

**Arms B and C use different `border` arity** — `main` predates the
`border(image)` signature change (commit `5c26781`) and still uses the bare
0-arg `border`. `bench_tacas19_main.imgql` and `bench_tacas19_incoming.imgql`
are otherwise identical; diff them if you need convincing there's no other
discrepancy.

## Running it

```bash
# Arm B (main) -- from within the main worktree
cd /tmp/vl2_main_baseline
export PYTHONPATH=implementation/python
sed 's/case_count = 40/case_count = 3/' bench_tacas19_main.imgql > /tmp/warmup.imgql
.venv/bin/python -m voxlogica.main run --no-cache /tmp/warmup.imgql   # untimed warmup
.venv/bin/python -m voxlogica.main run --no-cache bench_tacas19_main.imgql  # timed

# Arm C (incoming) -- from this repo's root
export PYTHONPATH=implementation/python PYTHON_GIL=0
sed 's/case_count = 40/case_count = 3/' tests/perf/scaling/abcd_comparison/bench_tacas19_incoming.imgql > /tmp/warmup.imgql
.venv/bin/python -m voxlogica.main run --no-cache /tmp/warmup.imgql
.venv/bin/python -m voxlogica.main run --no-cache tests/perf/scaling/abcd_comparison/bench_tacas19_incoming.imgql

# Arm D (plain Python)
.venv/bin/python tests/perf/scaling/abcd_comparison/plain_python_reference.py \
  --dataset-root /path/to/MICCAI_BraTS2020_TrainingData --cases 40 --warmup 3
```

All three use the SAME warmup convention (3 untimed cases, then a fresh
40-case timed run recomputing everything including 1-3) so cold-start costs
(JIT, imports, page-cache fill) are excluded uniformly. Arm A's protocol is
in `../vl1_comparison/README.md` (a separate warmup process, since VL1 has no
in-process warmup concept).

## Results (fmt-5000, 2026-07-31, idle host)

| arm | wall (40 cases) | per-case | mean Dice |
|---|---|---|---|
| A: VL1 | 8.16s | 0.204s | (different thresholds -- not compared, see vl1_comparison/README.md) |
| B: `main` | 56.08s | 1.402s | 0.823801585942116 |
| C: `incoming` (current) | **5.43s** | **0.136s** | 0.823801585942116 |
| D: plain Python | 19.23s | 0.481s | 0.823834606712500 |

A/B/D are the original 2026-07-31 measurements. C was refreshed on
2026-08-02 after the native-output and host-memory passes: 5.33/5.42/5.53 s,
mean 5.43 s, using the same warm-up/no-cache protocol. The previous published
C value was 6.22 s, so current `incoming` is 12.7% lower wall time (1.15x).
With the buffer pool disabled the same current code averaged 5.50 s; pooling's
1.3% difference is too small to claim separately as a stable speedup.

**Correctness cross-checks, not just timings:**
- B and C are **bit-identical** across all 40 cases (same `mean_dice` to 15
  significant figures) -- confirms the engine rewrite changed performance,
  not semantics.
- D agrees with B/C to 4 decimal places (0.82383 vs 0.82380). The residual
  ~3e-5 difference is attributable to minor floating-point path differences
  in the exact intensity/cast call sequence (verified NOT a logic error:
  a wrong operator direction or missing step would misplace the result far
  more than the 5th significant digit). Operator semantics (`pdt`/`distgeq`/
  `distleq`/`smoothen`) were confirmed against the real VL1 binary's
  `.<=`/`.>=` operators on a synthetic array before being trusted, not
  derived from the formulas alone -- see the manuscript for that check.

See the manuscript for the full interpretation and the investigation's
concluding remarks.
