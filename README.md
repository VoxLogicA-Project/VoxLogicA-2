# VoxLogicA

VoxLogicA is a symbolic, declarative computation language for building and executing dataflow plans (including large image-processing workloads).

Current runtime architecture:
- Symbolic reducer (`AST -> SymbolicPlan`)
- Dask execution runtime (single supported strategy)
- Stable primitive contract (`PrimitiveSpec`)
- Modular results database API (`~/.voxlogica/results.db` by default)
- Interactive REPL session runtime (CLI today, GUI-ready integration point)

## Quick Start

Run from repo root:

```bash
# Install uv once (https://docs.astral.sh/uv/)
# Example (macOS/Linux): curl -LsSf https://astral.sh/uv/install.sh | sh

# Deterministic environment sync (creates/updates .venv using .python-version + pinned requirements)
python3 bootstrap.py --with-test

# Show CLI help
./voxlogica --help

# Show version
./voxlogica version

# Run a program file
./voxlogica run test.imgql

# Run with legacy side-effect policy enabled (CLI only)
./voxlogica run --legacy test.imgql

# Start API server
./voxlogica serve

# Start modern dev mode (backend + Vite frontend, one command)
./voxlogica dev
```

## Safety Defaults

- Static resolution is strict: unknown callable names fail before execution.
- Non-legacy mode is default; side-effectful primitives are blocked unless CLI `--legacy` is set.
- `serve` always runs non-legacy policy and disables server-side save/export fields.
- Persisted values use the canonical `voxpod/1` JSON+Binary format (see `doc/spec/store-format-voxpod-v1.md`).
- Results DB schema/version mismatches trigger destructive recreation by design in this branch.
- Serve-mode read primitives are constrained to allowed roots:
  - `VOXLOGICA_SERVE_DATA_DIR` (primary root)
  - `VOXLOGICA_SERVE_EXTRA_READ_ROOTS` (optional comma-separated extras)

## Testing

```bash
# Full pytest suite
./tests/run-tests.sh

# Or direct
.venv/bin/python -m pytest
```

## Release Upgrade

```bash
# Validate pinned requirements, force-sync .venv, run full tests
python3 implementation/python/release_upgrade.py

# Sync only (no tests)
python3 implementation/python/release_upgrade.py --skip-tests
```

Python version policy:
- The canonical interpreter pin is [`.python-version`](/Users/vincenzo/data/local/repos/VoxLogicA-2/.python-version).
- Update that file (for example `3.12.8` -> `3.12.9`) and rerun bootstrap/release helper.

## Interactive REPL

```bash
./voxlogica repl --execution-strategy dask
# optional legacy mode:
./voxlogica repl --legacy
```

Useful REPL commands:
- `:help`
- `:load <file>`: load declarations/imports from file (no goal execution)
- `:run <file>`: load file and execute goals
- `:show`: display session context
- `:reset`: clear context
- `:quit`

When you evaluate an expression in REPL, VoxLogicA computes it and stores the result (or a representation payload when not directly serializable) keyed by node hash.

## Minimal Lazy Threshold Sweep Example

The repository includes test image data at `tests/data/chris_t1.nii.gz`.

This program computes the intensity range, builds a lazy symbolic sequence of all integer thresholds, and defines a lazy mapped sequence of thresholded masks:

```imgql
import "simpleitk"

img = ReadImage("tests/data/chris_t1.nii.gz")
mm = MinimumMaximum(img)
lo = index(mm,0)
hi = index(mm,1)

thresholds = range(lo, hi+1)
mk_mask(th) = BinaryThreshold(img, th, hi, 1, 0)
masks = map(mk_mask, thresholds)
n_thresholds = hi-lo+1
```

Why this is lazy/symbolic:
- `thresholds` and `masks` are represented as symbolic sequence computations in the plan.
- Materialization and paging are lazy; sequence pages are fetched on demand.

### Inspect the plan without executing

```bash
./voxlogica run tests/threshold_sweep.imgql --no-execute --save-task-graph-as-json /tmp/thresholds-plan.json
```

### Explore interactively

```bash
./voxlogica repl --execution-strategy dask
# then in the REPL:
:load tests/threshold_sweep.imgql
thresholds
lo
hi
```

The REPL previews sequence results and persists evaluated nodes in the results store.

## CLI Reference

Main commands:
- `version`
- `run <filename>`
- `repl`
- `list-primitives [namespace]`
- `serve`

For command-specific flags:

```bash
./voxlogica <command> --help
```

## Additional Documentation

- Developer docs: `doc/dev/`
- Module docs: `doc/dev/modules/`
- Python package docs: `implementation/python/README.md`
- API usage notes: `doc/user/api-usage.md`
- Language guide + example gallery: `doc/user/language-gallery.md`
- Serve studio dashboards: `doc/user/serve-studio.md`
