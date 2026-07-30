# Making free-threaded (no-GIL) Python the DEFAULT — implementor guidance

Status: **plan only, nothing implemented.** Branch: `incoming`.
Prerequisite reading: `doc/dev/free-threaded-handover.md` (especially §1's
single-writer concurrency argument and §5a's ITK-oversubscription finding).
That doc established free-threading *works* and *breaks the GIL ceiling*
(~12.7/24 cores → ~23.4/24 on fmt-5000). This doc is about promoting it from
an opt-in experiment to the default path, and it exists because that promotion
has **five traps that fail silently** — a naive "flip the pin" will appear to
work while changing nothing, or worse, will reuse a stale measurement.

---

## 1. Verified facts (checked 2026-07-30, not recalled)

| # | Fact | How verified |
|---|------|--------------|
| F1 | **The full dependency set resolves on cp314t** — 87 packages including `torch==2.10.0`, `nnunetv2==2.6.2`, `playwright==1.58.0`, `mcp==1.26.0`, `uvloop`, `scipy` — with *only* the two pin bumps in F2. | `uv pip install --dry-run --python .venv-ft/bin/python -r requirements.txt`, exit 0 |
| F2 | `requirements.txt` pins `numba==0.64.0` and `SimpleITK==2.5.2`; **neither has a cp314t wheel**. Floors `numba>=0.66.0` / `SimpleITK>=2.5.5` are the first that do. | same dry-run: fails on `simpleitk==2.5.2` "no wheels with a free-threading compatible ABI tag" |
| F3 | `looping_experiment/run_iter.sh` **already implements the whole FT recipe** behind `VOXLOGICA_VENV=ft`: `.venv-ft` + `PYTHON_GIL=0` + `ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1`. That file is **untracked** (`looping_experiment/` is in `.git/info/exclude`), so the recipe is host-local knowledge outside git. | read the script on fmt-5000 |
| F4 | The **default** path (`./voxlogica` → `bootstrap.py` → `.venv`) is GIL-enabled on both hosts. `.python-version` = `3.14`. | `sys._is_gil_enabled()` → `True` |
| F5 | `.venv-ft` on both hosts is **minimal** (~59 pkgs; no torch/fastapi/mcp/playwright). Not promotable as-is — the default venv needs the full set. | `ls site-packages` on both |
| F6 | `kernels.py` has only `SetGlobalDefaultThreader("Pool")` — **no thread cap**. §5a's verified ~9% win is still unwired. | grep |
| F7 | `sitk.ProcessObject.SetGlobalDefaultNumberOfThreads` exists; ITK's default on the Mac is **18 = every logical core**. | runtime check |

---

## 2. The five traps

### T1 — `bootstrap.py` cannot parse any free-threaded version spec
`_parse_version_spec()` does `int(part)` over a dot-split. Both candidate
spellings crash:
- `"3.14t"` → `int("14t")` → `ValueError`
- `"3.14.4+freethreaded"` → `int("4+freethreaded")` → `ValueError`

Verified. So this is a **code change, not a pin change**. `uv` accepts both
spellings (`uv python list` shows `cpython-3.14.4+freethreaded-macos-aarch64-none`
already installed locally); `bootstrap.py` is the only thing that can't.

### T2 — flipping the pin will NOT recreate an existing `.venv`
`_ensure_venv()` compares **only version tuples**, and a free-threaded build
still reports `sys.version_info == (3, 14, 4)` — identical to the GIL build.
So `recreate` evaluates `False` and the existing GIL venv is silently kept.

Fix: include `sysconfig.get_config_var("Py_GIL_DISABLED")` in both the
recreate comparison and `ENV_STAMP`.

### T3 — the calibration cache is GIL-blind (**the worst trap**)
```
MachineFingerprint = {cpu_model, p_cores, logical_cpus, total_ram_bytes, kernel_release}
```
There is **no interpreter-build field**. Switching to free-threading changes
none of these, so `key()` is unchanged and `load_cached_threads()` happily
returns **a thread count measured under the GIL ceiling** — defeating the
entire purpose of the switch, with no warning.

Fix: add the build (`Py_GIL_DISABLED`) to `MachineFingerprint`. This
invalidates all existing cached calibrations, which is *correct and desired* —
every cached number predates the change.

### T4 — `PYTHON_GIL=0` cannot be set from Python
It is read at interpreter init. SimpleITK **and** numba both re-enable the GIL
on import without it (handover §5). It must be in the `voxlogica` wrapper
before `exec`, or `-X gil=0` on the exec line. An earlier suggestion in this
session to set it via `os.environ` in `main.py` was **wrong** — too late.

### T5 — do not compute the ITK cap arithmetically
The obvious `cpu_count // engine_workers` is exactly the naive per-core split
this project already disproved: `topology.py` exists *because* DRAM bandwidth
saturates well before logical-core count (measured: 16 threads 6.85s vs 24
threads 7.84s **and ~2x the CPU-seconds**). The ITK cap interacts with the
engine's worker count and must be **measured**, not divided. Treat it as a
calibration axis or as one env-overridable knob with a measured default.

### T6 — calibration does not run on macOS at all
`run_calibration()` raises `RuntimeError` when `p_cores == 0`, and `p_cores`
comes from the Linux-only sysfs path `/sys/devices/cpu_core/cpus`. On macOS
`p_cores == 0`, so:
- `voxlogica calibrate` is **unavailable** on the Mac;
- `default_concurrency()` returns plain `os.cpu_count()` (Mac: `balanced == logical == 18`).

**All calibration and all authoritative benchmarking must happen on fmt-5000.**

---

## 3. Calibration behaviour (for context — unchanged by this work)

- `default_concurrency()` checks the fingerprint-keyed cache first; on a hit it
  uses the measured value.
- On a miss it falls back to the `"balanced"` heuristic (P-cores + half the
  E-cores) and prints a one-line `voxlogica calibrate` hint **once per process**.
- Calibration is **never auto-run** — deliberate: a sweep costs tens of seconds
  and must not ambush a 7-second job.
- Re-calibration is **fingerprint-triggered, not time-triggered** (hardware
  doesn't drift). T3 is precisely a hole in that trigger.

---

## 4. The benchmark already run is NOT valid — do not cite it

For the record, a 1→18 worker sweep on the Mac (4 BraTS cases, full brats015
grid, `--no-cache`):

| threads | wall (s) | naive "speedup" |
|---|---|---|
| 1 | 177.2 | 1.0× |
| 2 | 121.5 | 1.46× |
| 4 | 88.4 | 2.0× |
| 8 | 72.7 | 2.4× |
| 12 | 73.9 | 2.4× |
| 18 | 51.5 | 3.4× |

**Three independent reasons this does not measure engine scaling:**
1. **ITK internal threading was uncapped** (F7), so the `--threads 1` baseline
   already consumed ~3.6 cores (638s CPU / 178s wall). The denominator is not
   serial, which deflates every ratio.
2. It ran on the **GIL** build (F4) — the ceiling the whole exercise is about.
3. It ran on **macOS**, where `balanced == logical == 18` and calibration is
   impossible (T6).

The honest number requires Phase C3 below. Anyone quoting "3.4×" is quoting an
artifact of an uncapped ITK pool.

---

## 5. Ordered work plan

### Phase A — make the recipe expressible (default unchanged)
- **A1.** `bootstrap.py`: accept FT specs (T1); add `Py_GIL_DISABLED` to the
  recreate comparison and `ENV_STAMP` (T2); add a `VOXLOGICA_GIL=1` escape
  hatch for hosts where cp314t misbehaves.
- **A2.** `requirements.txt`: `numba>=0.66.0`, `SimpleITK>=2.5.5` (F2). If the
  GIL build must keep the old pins, use environment markers rather than a
  blanket bump.
- **A3.** `voxlogica` wrapper: export `PYTHON_GIL=0` when the resolved venv is
  free-threaded — **detect it** via `sysconfig`, don't hardcode (T4).

### Phase B — fix calibration before trusting any number
- **B1.** Add interpreter-build to `MachineFingerprint` (T3). Expect and accept
  cache invalidation.
- **B2.** Decide the ITK cap policy (T5) and wire it next to the existing
  `SetGlobalDefaultThreader("Pool")` in `kernels.py` (F6). §5a measured ~9%
  wall-clock and a ~3.5-4x contention cut from `=1` at 24 workers — but that
  is one point on a curve, not the curve.
- **B3.** Recalibrate on fmt-5000 (T6).

### Phase C — validate (nothing above is trustworthy until this passes)
- **C1.** Build a **full-dep** FT venv (F1/F5) and run the unit suite on both
  builds; expect parity. Prior Phase-1 parity was only ever shown against the
  *minimal* venv.
- **C2.** Re-run the handover's Phase-2 stress loop (20× byte-identical,
  eviction-heavy fan-out) under the **real** dependency set.
- **C3.** The honest scaling sweep on fmt-5000: FT, ITK capped, post-B3
  calibration. **This is the number that answers "how good is the engine".**

### Phase D — flip the default and consolidate
- **D1.** `.python-version` → `3.14t`.
- **D2.** Fold F3's `VOXLOGICA_VENV=ft` knowledge into the tracked path, so the
  untracked `run_iter.sh` branch becomes redundant rather than authoritative.
- **D3.** Update `free-threaded-handover.md` §5b, which currently lists this as
  un-acted-on.

Phases A2/A3 are ~10 minutes. A1 and B1 are small but load-bearing. B2 is a
design decision. C3 is the only thing that produces a defensible speedup claim.

---

## 6. Scope note

`_scratch_scaling/` on the Mac and `looping_experiment/_bench_scaling.imgql`
are throwaway artifacts of the invalid §4 benchmark — safe to delete. The
in-flight `brats020.imgql` iteration on fmt-5000 was killed on 2026-07-30 to
free the box for this work; it was running under `.venv-ft` via
`VOXLOGICA_VENV=ft` and will need restarting.
