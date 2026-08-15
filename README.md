# VoxLogicA

VoxLogicA is a symbolic, declarative computation language for building and executing dataflow plans (including large image-processing workloads).

Current runtime architecture:
- Symbolic reducer (`AST -> SymbolicPlan`)
- Dask execution runtime (single supported strategy)
- Stable primitive contract (`PrimitiveSpec`)
- Modular results database API (`~/.voxlogica/results.db` by default)
- Interactive REPL session runtime (CLI today, GUI-ready integration point)

## Examples

Runnable, commented programs live in the [example gallery](doc/gallery/README.md),
ordered as a reading path from the language basics to a complete study: a BraTS
threshold sweep that scores every case, reports the distribution of the per-case
best threshold, and exports the worst cases at three anatomical levels, with and without ground truth.

## Quick Start

Run from repo root:

```bash
# No prerequisites beyond python3 and git: if uv (https://docs.astral.sh/uv/) is not
# on PATH, bootstrap downloads a checksum-verified copy into .cache/uv/bin. That needs
# no root and writes nothing outside the checkout. Set VOXLOGICA_NO_UV_DOWNLOAD=1 to
# require a preinstalled uv instead (offline hosts), or VOXLOGICA_UV=/path/to/uv.

# Deterministic environment sync (creates/updates .venv using .python-version + pinned requirements, including pytest)
python3 bootstrap.py

# Show CLI help
./voxlogica --help

# Show version
./voxlogica version

# Run a program file
./voxlogica run test.imgql

# Run with legacy side-effect policy enabled (CLI only)
./voxlogica run --legacy test.imgql

# Open the workspace UI (see "The workspace" below)
./voxlogica

# Serve the UI without opening a window (Ctrl-C to stop)
./voxlogica serve

# Speak MCP on stdio for whichever instance is running
./voxlogica mcp
```

## The workspace

`./voxlogica` with no arguments opens a workspace: a bento board of cards over
an `.imgql` file. Nothing is asked on the way in.

```bash
./voxlogica                      # a new workspace, in a window
./voxlogica path/to/study.imgql  # serve an existing one (same as `serve <file>`)
./voxlogica run program.imgql    # compute, with the UI attached (see below)
```

**Where your work lives.** Files live in a library in the place your platform
keeps application data. A project is a folder in it and a file is an `.imgql`
inside; new files start loose at the top, and the sidebar lists all of them —
one opens in the pane at a time, so there are no tabs. Drag a file onto a project
to move it.

| | |
|---|---|
| macOS | `~/Library/Application Support/VoxLogicA/workspaces/<timestamp>/` |
| Linux | `$XDG_DATA_HOME/voxlogica/workspaces/<timestamp>/` (default `~/.local/share`) |
| Windows | `%LOCALAPPDATA%\VoxLogicA\workspaces\<timestamp>\` |

Set `VOXLOGICA_HOME` to put the library somewhere else. Because projects are
plain folders, a project *is* something you can put under version control as it
stands.

**Saving.** There is none: the file is the document, written automatically and
debounced. Nothing is ever "unsaved".

**Moving it into a repository.** *Move…* at the bottom of the window opens the
system's own save panel and takes the file out of the library; a folder that
existed for that one file goes with it, images and all. The layout lives in the
file's own `//@card` comments, so from then on it diffs, merges and commits like
any other source. The button beside it shows the file in your file manager.

**Node.** The UI is built on first use, and if there is no usable Node on `PATH`
VoxLogicA fetches an official one for your platform into its own data directory
— checksum-verified against Node's published `SHASUMS256.txt`, unpacked per
version, never installed system-wide. Nothing to install, nothing to add to
`PATH`. `VOXLOGICA_NODE=/path/to/node` uses your own; `VOXLOGICA_NO_NODE_DOWNLOAD=1`
refuses the download and tells you what to install instead.

**With a computation.** `./voxlogica run program.imgql` prints its URL and then
behaves exactly as it always did — same stdout, same exit code. If you open the
UI it keeps serving until the last window closes; if nobody is watching it exits
the moment the run ends.

**Agents.** The instance registers an MCP server with every installed client it
finds (Claude Code, Claude Desktop, Cursor, Codex) on first run, so an agent can
see the same workspace you are looking at and drive it through the same named
actions the UI uses. `VOXLOGICA_NO_MCP_REGISTER=1` turns that off.

**Ports.** The UI takes the first free port from 10001 upward; `--ui-port` picks
a different starting point. Everything binds to loopback only.

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

# Runtime-only sync if you explicitly want to skip test tooling
python3 bootstrap.py --runtime-only
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
- **Example gallery** (runnable, commented programs): [`doc/gallery/README.md`](doc/gallery/README.md)
- Language guide (narrative index): `doc/user/language-gallery.md`
- Serve studio dashboards: `doc/user/serve-studio.md`
- VS Code MCP setup for the UI inspector: `doc/user/vscode-mcp-ui-inspector.md`
