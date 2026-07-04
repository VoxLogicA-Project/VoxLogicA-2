# INCOMING-LOG — handover for a fresh Claude session

Written 2026-07-04. **Why this file exists:** fmt-5000 (the remote machine that
hosts the BraTS datasets, the Python venv with SimpleITK, and the
`looping_experiment/` directory) is down because of a power blackout. The user
is switching to a **new Claude account, on this same Mac, same repo
checkout**, to keep working on the issues below while fmt-5000 is
unreachable. The new session has **no memory of the prior conversation** — this
file is the entire handoff. Read it fully before touching anything.

If you are the new session: do not assume you can immediately resume the
BraTS experiments — read the "What you can and cannot do right now" section
first.

---

## 0. What you can and cannot do right now

- **This Mac checkout has no experiment environment.** `looping_experiment/`
  does not exist here (it's local-only on fmt-5000, deliberately excluded from
  git via `.git/info/exclude` on that machine — see §7). There is no
  `.venv` with SimpleITK installed here, no `pytest` installed, no BraTS
  dataset. `python3 -m pytest` fails with `No module named pytest`.
- **fmt-5000 is down** (blackout). Nothing that requires running VoxLogicA on
  real images, or touching `looping_experiment/*.imgql`, or checking the
  oracle numbers, can happen until it's back up. Don't invent numbers or
  claim something was tested if it wasn't.
- **What you *can* do here:** read/edit the engine and execution-strategy
  source in `implementation/python/voxlogica/`, review/fix code, manage git
  branches and GitHub PRs/issues (`gh` CLI is authenticated against
  `VoxLogicA-Project/VoxLogicA-2`), update docs, and — if useful — set up a
  local venv (`pip install -e .[dev]` or similar; check `pyproject.toml`) to
  at least run the non-ITK unit tests if SimpleITK isn't available.
- **When fmt-5000 comes back**: ssh alias is `fmt-5000` (see `~/.ssh/config`:
  `fmt-5000.isti.cnr.it`, user `vincenzo`, port `10122`). The repo there lives
  at `~/data/local/repos/VoxLogicA-2` (user moved it there from
  `/home/VoxLogicA` at some point — see §8). Check out `incoming`, `git pull`,
  and re-verify everything in §6 before trusting any prior result.

---

## 1. Repository state right now (verified 2026-07-04)

```
Current branch: incoming   (clean working tree, fully pushed to origin/incoming)
Main branch:    main
Remote:         origin = https://github.com/VoxLogicA-Project/VoxLogicA-2.git
```

Local `incoming` == `origin/incoming` exactly (same commit
`3b4330fe2849cff4bf82ff3e6250f5767e2ff79f`). Nothing uncommitted, nothing
unpushed.

Branches that exist (local + remote), for reference:
```
feat/async-executor        (merged into incoming)
feat/dynamic-expansion      (merged into incoming, PR #23, MERGED)
feat/inrun-result-memo      (merged into incoming via merge commit; PR #17 still OPEN on GitHub against main)
feat/plan-expansion         (merged into incoming, PR #21, MERGED)
feat/two-level-cache        (merged into incoming via merge commit; PR #18 still OPEN on GitHub, based on #17)
feat/unified-execution      (merged into incoming, PR #25, MERGED)
fix/itk-pool-threader-segfault (merged, PR #16, MERGED)
main
incoming                    (current work branch — integration branch, not main)
```

**Important nuance on #17/#18:** these two PRs are still shown as `OPEN` by
`gh pr list` (base branches `main` and `feat/inrun-result-memo` respectively —
they were never merged/closed on GitHub, they were folded into `incoming` via
direct merge commits `7144d6f` and `56094f7`). Their code IS already present on
`incoming`. Don't re-merge them; if/when `incoming` is eventually merged to
`main`, these two PRs should just be closed (their content will already be in
main via the incoming merge) — check with the user before closing PRs.

---

## 2. `incoming` branch commit history (oldest → newest), what each did

This is the full stack, in order, since `main`:

1. `0017424` / `09da661` — storage: in-RAM result memo (#17), then bounded to
   a two-level memory/disk LRU cache (#18). Fixes: lazy interpreter was
   dropping image-like values behind a node-id placeholder, causing
   recomputation of shared subtrees on every demand under `--no-cache`.
2. `56094f7`, `7144d6f` — merge commits folding #17/#18 into `incoming`.
3. `962c746` (#19) — replaced the recursive DFS evaluator with an async
   task-graph executor (`asyncio` + `ThreadPoolExecutor`), so ITK kernels
   (which release the GIL) actually run in parallel.
4. `5c26781`, `6de376d` — **`border(img)`, `x(img)`, `y(img)`, `z(img)`** now
   take an explicit image argument instead of reading a global "current base
   image" (`_require_base()`). This was necessary because under the async
   executor, node evaluation order is no longer guaranteed, so the old
   zero-arg global-state primitives crashed with "No model loaded" whenever
   they ran out of the expected order. All call sites in the `.imgql` files on
   fmt-5000 (`utils.imgql`, `brats*.imgql`) were updated to pass the image
   explicitly (e.g. `border(flair)`, `border(r)`).
5. `cd686a3`, `c5fa99c`, `fefa453`, `303528e`, `087cdcd`, `a810fe3` — hardening
   of the async executor: don't swallow exceptions, don't cache closures/
   constants, bound concurrency and memory via backpressure/LIFO worker
   ordering, free consumed intermediates.
6. `98c88c7` — merge of `feat/async-executor` into `incoming`.
7. `4627da6` — reducer: let user-defined operators shadow primitive aliases.
8. `92424eb` (#20/#21, `feat/plan-expansion`) — **static loop expansion**:
   `for` loops over a constant-length iterable are unrolled into independent
   DAG nodes at reduce time (parallelizable, cacheable per element), capped by
   `_FOR_EXPANSION_CAP` (now the `--for-expansion-cap` CLI flag, default
   4096).
9. `ab312ec` — merge of `feat/plan-expansion` into `incoming`.
10. `8a1c8d7`, `02c00bc`, `9c42b53` (#22, `feat/dynamic-expansion`) —
    **dynamic loop expansion**: `for` loops whose iterable is only known at
    runtime (`for_loop` nodes) are expanded into DAG nodes the instant the
    iterable's value materializes, splicing new nodes into the *live* running
    schedule. Fixed nested-expansion bugs around capture eviction and
    ready-gating.
11. `bcbc523`, `4aed4ec` — tqdm total grows as dynamic expansion splices in
    new nodes; merge of `feat/dynamic-expansion`.
12. `4b33b14` — design doc `doc/dev/unified-computation-engine.md` (see §5,
    **now slightly stale** — written before the CLI-flag refactor in commit
    13 below; it still says `VOXLOGICA_ENGINE=1`, which no longer exists).
13. `5742583`, `ed1b7de`, `801c46a`, `1ac9097` (#24, `feat/unified-execution`)
    — the **live computation engine** (`voxlogica/engine/`): a persistent,
    content-addressed, priority-scheduled evaluator, opt-in alongside the
    proven `lazy` strategy. See §4 for the deep dive; §3 lists every deadlock
    class that was found and fixed during this work.
14. `d7aad37` — engine: live tqdm progress bar with growing total + current
    op label.
15. `48a82cc` — merge of `feat/unified-execution` (#24) into `incoming`.
16. `3b4330f` (**HEAD of incoming, current tip**) — **CLI flags replace env
    vars**. This is the most recent change and invalidates any earlier
    instructions that mention environment variables. See §3 "BREAKING" note.

---

## 3. ⚠️ BREAKING: env vars were replaced by CLI flags in the last commit

The very last commit on `incoming` (`3b4330f`, "cli: replace execution env
vars with run options; per-instance threads") removed **all** of the
following environment variables and replaced them with `voxlogica run` CLI
flags:

| Removed env var | Replacement |
|---|---|
| `VOXLOGICA_ENGINE=1` | `--engine` |
| `VOXLOGICA_MAX_CONCURRENCY` / similar | `--threads N` |
| `VOXLOGICA_DYNAMIC_EXPANSION` | `--dynamic-expansion` / `--no-dynamic-expansion` (default: on) |
| `VOXLOGICA_FOR_EXPANSION_CAP` | `--for-expansion-cap N` (default 4096; 0 disables) |
| `VOXLOGICA_ENGINE_MEMORY_MB` | `--memory-mb MB` (default: 60% of system RAM) |
| `VOXLOGICA_ENGINE_DEBUG=1` | `--engine-debug` |

Storage-tuning env vars (e.g. `VOXLOGICA_MEMORY_CACHE_CAPACITY` for the
two-level cache) and the external nnUNet env vars are **unchanged**.

**If you (or any doc, or any earlier chat transcript) see `VOXLOGICA_ENGINE=1`
or any of the left-hand-column vars — that's stale. Use the flags.** In
particular `doc/dev/unified-computation-engine.md` still says
`VOXLOGICA_ENGINE=1` in its status line — worth a one-line fix when convenient
but not urgent.

Correct current invocations (once fmt-5000 is back and the venv exists):

```bash
# lazy strategy (default), with tqdm progress, no cache:
cd looping_experiment && ../implementation/python/.venv/bin/voxlogica run --no-cache brats010.imgql

# engine strategy, full CPU, 8 GB live-tier budget:
cd looping_experiment && ../implementation/python/.venv/bin/voxlogica run --no-cache --engine --memory-mb 8000 brats010.imgql

# engine strategy with stuck-frontier diagnostics on failure:
... --engine --engine-debug ...

# cap threads explicitly (both strategies):
... --threads 8 ...
```

(Adjust the venv path to wherever it actually lives on fmt-5000 —
last known location was `~/data/local/repos/VoxLogicA-2/.venv` after the user
moved things from `/home/VoxLogicA`, see §8. Verify with `which voxlogica` or
`ls .venv/bin/` before trusting this path.)

I verified by reading the current source (not from memory) that:
- `main.py` `run_command` wires `args.engine`, `args.threads`,
  `args.memory_mb or None`, `args.engine_debug`, `args.dynamic_expansion`
  straight into `ExecutionEngine(...)`.
- `ExecutionEngine.__init__` (`execution.py`) branches on `use_engine` to
  build either `EngineExecutionStrategy(registry, storage, threads=,
  memory_mb=, debug=)` or `LazyExecutionStrategy(registry, storage, threads=,
  dynamic_expansion=)`.
- `EngineExecutionStrategy.run()` constructs `ComputationEngine(registry=,
  backend=, max_concurrency=self.threads, memory_mb=self.memory_mb,
  progress=True, debug=self.debug)`.
- `ComputationEngine.__init__` (`engine/core.py`) **does** accept
  `max_concurrency`, `memory_mb`, `progress`, `debug` — I confirmed this
  directly by reading `core.py`. **A prior open concern from an earlier
  session — that `strategy.py` might pass args `core.py` doesn't accept — is
  resolved/moot; the signatures match.** No fix needed here.

---

## 4. The live computation engine (`voxlogica/engine/`) — how it works

Design doc: `doc/dev/unified-computation-engine.md` (mostly accurate, see the
one stale env-var line above). Short version, confirmed against current code:

- **`NodeTable`** (`node_table.py`): hash-consed nodes (Merkle SHA-256 keyed,
  see `voxlogica/lazy/hash.py`), a live-value tier (`self.values`,
  byte-tracked via `live_bytes`), and `self.completed` (monotonic set of
  finished node ids). Enforces the **no-double-computation invariant**:
  `begin(node_id)` raises `DoubleComputationError` if the node is already
  running or already has a value.
- **`ComputationEngine`** (`core.py`): one asyncio event loop, single-writer
  over all scheduling maps (no locks needed). `submit()` registers a goal and
  BFS-schedules its unmaterialized subgraph (pruned at already-completed or
  already-persisted nodes). A pool of `max_concurrency` async workers drains a
  priority queue (`-priority, seq, node_id` tuples so higher priority and
  older insertion win ties).
- **THE key correctness principle, load-bearing across every deadlock fix
  below:** readiness (`_register`) is gated on whether a dependency is in
  `table.completed` — a **monotonic fact** — never on whether its value is
  currently resident in `table.values`. Values can be evicted under memory
  pressure at any time; `_rematerialize()` transparently recomputes or
  reloads them on demand. Gating on residency instead of completion is what
  caused every deadlock found during development (see below) — if you touch
  this code, do not regress that invariant.
- **Eviction** (`_relieve_memory`): an `OrderedDict` of consumer-exhausted,
  non-goal nodes (`_releasable`), oldest-first; only evicted while
  `table.live_bytes > memory_limit_bytes(memory_mb)`. Budget defaults to 60%
  of system RAM (`engine/memory.py`), overridable via `--memory-mb`.
  **Eviction is memory-pressure-driven (byte budget), not eager
  consumer-count-driven** — this was an explicit design decision the user
  asked for (see conversation history §"ok and make sure eviction happens
  when memory is full above a certain threshold not by pre-determined
  counts").
- **Dynamic expansion** (`_expand`, delegates to `expander.py`): when a
  `for_loop`/`map`/`filter` node's iterable materializes, its body is spliced
  into the *live* schedule as new nodes (not interpreted to values directly —
  the whole point of "one semantics", see the design doc §2). The iterable is
  always rematerialized first so expansion can't fail on an evicted value.
- **Priorities** (`priority.py`, `prioritize()` API): a query can be raised
  above other in-flight work; the bump propagates to its not-yet-completed
  transitive dependencies. This is implemented but not yet exercised
  end-to-end in a live/REPL setting — noted as a roadmap item in the design
  doc §"Validated" vs. future work.

### Deadlocks found and fixed during development (for context if new bugs surface)

All of these were fixed by consistently applying "gate on `completed`, never
on value residency":

1. Lazy strategy: `register()` was gated on `dep in prepared.values`, but an
   evicted-yet-completed dep is missing from `values` → deadlock. Fixed to
   also check `dep in prepared.completed_nodes` + rematerialize.
2. Lazy strategy: closure captures evicted before an expansion read them.
   Fixed via `pin_closures()` on discovered nodes.
3. Lazy strategy: a loop-invariant constant node evicted after the first
   query's consumers ran, then a second query needed it → deadlock. Fixed via
   `rematerialize()` + pinning for evicted-completed deps.
4. Engine: multi-query scheduling deadlocked for the same residency-vs-
   completion reason; fixed the same way in `_register`.
5. Engine: a **stale shadowed `_rematerialize` method** (duplicate empty
   definition later in the file) silently shadowed the real one, making every
   alias-forward return `None`. Fixed by deleting the stale duplicate
   (commit `1ac9097`).
6. Engine: nested dynamic expansion — an inner loop's capture got evicted
   before the outer expansion finished reading it. Fixed by always
   rematerializing the iterable first in `_expand()`, and pinning closure
   captures.

If a new hang/deadlock appears in the engine, **first check whether some new
code path gates on `nid in table.values` or `nid in some_values_dict` instead
of `nid in table.completed`** — that has been the root cause every single
time so far.

---

## 5. What's proven vs. what's still rough

From the design doc's own "Validated" line plus direct verification of the
code:

**Validated (per design doc, engine team's own claim; not independently
re-verified by me today since fmt-5000 is down):**
- Single- and multi-query evaluation
- Nested runtime loops
- Real-data oracle matches `lazy` exactly (was checked against actual BraTS
  runs on fmt-5000 before the blackout)
- 18 unit tests under `voxlogica/engine/` (location: check
  `implementation/python/tests/` — I have not located/run these today; do so
  once a venv is available)
- Correctness under a forced 1 MB / 300 MB live-tier budget with real image
  eviction + rematerialize

**Known rough edges / roadmap (not bugs, just incomplete):**
- `prioritize()` exists but isn't exercised end-to-end (no live REPL surface
  built yet that would call it interactively)
- The engine's own doc calls itself the up-and-coming default but says
  explicitly: *"the proven `lazy` strategy remains the default until the
  engine has had broad real-workload validation"* — **do not flip the
  default without the user's explicit sign-off.**
- `doc/dev/unified-computation-engine.md` line 3 still says
  `VOXLOGICA_ENGINE=1` — stale, see §3.

---

## 6. Once fmt-5000 is back — verification checklist

Do this before trusting anything above or reporting old results as current:

1. `ssh fmt-5000`, `cd ~/data/local/repos/VoxLogicA-2`, `git fetch && git
   checkout incoming && git pull` — confirm it lands on `3b4330f` (or later,
   if the user pushed more from the Mac session in the meantime).
2. Confirm the venv still has SimpleITK etc. (`.venv/bin/voxlogica
   list-primitives`).
3. Run `brats010.imgql` (see §7) with `--no-cache` under both `lazy`
   (default) and `--engine`, confirm they produce the **same** average oracle
   number (`avg_oracle_kept`), and confirm the engine run doesn't deadlock or
   OOM under a real `--memory-mb` budget.
4. Confirm CPU utilization actually fills all cores during the async run (the
   user's original ask when creating `incoming`: "test the combination;
   switch fmt-5000 to incoming and test if it fills the cpu correctly
   without gaps").
5. Only after that, resume oracle-improvement work (see §7 — currently
   0.8676, target ~0.9, still has headroom).

---

## 7. The BraTS experiment context (lives entirely on fmt-5000, `looping_experiment/`, NOT in git)

This directory is intentionally **not tracked in git** — excluded via
`.git/info/exclude` **on fmt-5000 itself** (not in this Mac checkout's
exclude file, which is stock/empty of project rules). Rationale, verbatim
from the user: *"don't push in the study partial analyses or it will be a
nightmare to publish the paper."* Do not try to add it to git.

Key facts (from long-running prior work, unverifiable until fmt-5000 is
back — treat as "as of the last session," not current truth):

- **Dataset**: switched from BraTS2019 HGG (259 cases) to **BraTS2020** (369
  cases), naming `BraTS20_Training_NNN_*.nii.gz`. Path was last known as
  `/home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData`, but the user later
  said *"I moved it to /home/VoxLogicA"* — re-verify the actual current
  dataset path on fmt-5000 before assuming either location.
- **Goal**: non-learning FLAIR whole-tumour segmentation via VoxLogicA spatial
  logic operators, calibrated against an **oracle** — sweep 1-2 influential
  threshold parameters per case, take the per-case max score, average across
  cases. The gap between the oracle ceiling and any fixed-threshold procedure
  is the calibration headroom still to close.
- **`utils.imgql`** (shared helpers, factored out per the user's explicit
  request to clean/comment all `brats*.imgql` and factor common functions):
  `fill_holes(r)`, `hysteresis_seg(vi, g)`, `vi_levels`, `vi_candidates(g)`,
  `vi_volumes(g)`, `vi_stability(g)`. (MSER-related helpers were **removed**
  — see next bullet.)
- **MSER was investigated and abandoned**: it only improved the oracle match
  by +0.004 over a fixed threshold — not worth the complexity. Per the user's
  explicit instruction, the project now focuses exclusively on the
  best-oracle ceiling; calibration (finding the actual right threshold
  without an oracle) is deferred to later.
- **`brats009.imgql`**: VI-level oracle for what was called "brats008" in an
  earlier iteration. Last run on the `lazy` strategy with `--no-cache`; has
  **not** been re-run under the engine strategy — worth doing once fmt-5000
  is back, to cross-check correctness at scale (per an open item from the
  prior session).
- **`brats010.imgql`**: 2-parameter oracle sweep
  (`hyper_levels=[0.93,0.95,0.97] × vi_grid=[0.82..0.94]`) over `{flair,
  T2-trimmed}`, evaluated on the **first 30 cases** of BraTS2020
  (`eval30 = [0..29]`), with a suitability filter (`keep(g)`) excluding
  cases below a minimum seed-voxel threshold. Result as of the last run:
  **`avg_oracle_kept = 0.8675745613144829`** — i.e. ~0.868, below the ~0.9
  target ceiling the user has in mind from earlier exploration. This is the
  number any new run should be compared against.
- **Suitability filtering**: only exclusion criteria considered principled so
  far is FLAIR-isotropy-based; other automated case-exclusion heuristics were
  reviewed and rejected as unprincipled (see the "BraTS suitability test"
  memory file for the full reasoning, referenced below).
- **imgql authoring rule the user set explicitly**: every `.imgql` file must
  be commented; threshold sweeps belong in ImgQL itself (`for`/`argmax`), not
  hand-rolled in Python.
- **Per-case progress requirement**: every `brats*.imgql` must print
  per-case Dice as each case completes, not just the final averaged number —
  this was an explicit user request; check it's still honored in
  `brats009.imgql`/`brats010.imgql` if you touch them.

If you need more detail on any of the above, the user's memory files (this
Claude account's own persistent memory, separate from this log) already
contain compact writeups: `looping-experiment-workflow.md`,
`brats-segmentation-findings.md`, `clinical-contouring-guidelines.md`,
`brats-suitability-test.md`, `voxlogica-dt-segfault-fix.md`,
`lazy-fold-not-a-sequence-op.md`, `feedback-add-progress-prints.md`,
`brats-oracle-approach.md`, `feedback-imgql-style.md`,
`project-mser-abandoned.md`, `project-dataset-brats2020.md`. These are at
`~/.claude/projects/-Users-vincenzo-data-local-repos-VoxLogicA-2/memory/` and
should already be loaded into a new session's context automatically (they are
account-level, not conversation-level, so they may or may not carry over to
the *new* Claude account the user is switching to — if the new account has no
memory system populated yet, this section is the substitute).

---

## 8. Miscellaneous facts / gotchas from the prior session

- **`VOXLOGICA_ENGINE=1` is gone** — see §3. This supersedes an earlier
  hard rule ("study-critical constraint: engine is opt-in via env var,
  default lazy unchanged") — the *substance* of that rule (engine is opt-in,
  lazy is default) still holds, just via `--engine` flag now, not an env var.
- The dataset/repo location on fmt-5000 has moved at least once
  (`/home/VoxLogicA` → user later said `~/data/local/repos/VoxLogicA-2` for
  the repo, and mentioned moving the dataset to `/home/VoxLogicA` — these
  two statements are from different points in time and may not be
  simultaneously accurate). **Do not assume a path; `ssh` in and check.**
- `border()`, `x()`, `y()`, `z()` primitives (`voxlogica/primitives/vox1/`)
  were changed from zero-arg-with-global-state to explicit-image-argument.
  If you see any `.imgql` file (on fmt-5000, in `looping_experiment/`) still
  calling `border()`/`x()`/`y()`/`z()` with no argument, it's stale and will
  crash with "No model loaded" — fix the call site to pass the image
  explicitly, per the pattern already used in `utils.imgql`.
- `DoubleComputationError` (in `node_table.py`) is intentional and should
  never be caught/suppressed — it means the scheduler tried to compute the
  same content-addressed node twice, which is a scheduler bug, not a
  transient condition.
- tqdm progress bar in both strategies shows a "nodes: N%" percentage against
  a **growing total** (dynamic expansion increases the denominator as it
  splices in new nodes) and the current node's operator as the postfix
  label.
- Number formatting bug **#8** (F# port only: `3.14` → `3.1` in DAG JSON
  output) is old, unrelated to any of the above, still open, low priority.
- Issue **#14** ("no in-RAM memo, no node-level parallelism") is the parent
  issue that #17, #18, #19 all trace back to (`Refs #14` in each PR body) —
  it's still open on GitHub; it should probably stay open until the engine
  (or a decision to keep `lazy` as the permanent default) closes it out, or
  be explicitly re-scoped. Don't close it without checking with the user —
  §3.1/§3.2 defects are fixed, but §3.3 (double serialize/write) and general
  node-level parallelism maturity are still open per the PR #17 description.

---

## 9. Suggested immediate next steps for the new session

1. Re-read this file fully, then check with the user what they actually want
   worked on right now (git/PR hygiene? doc fixes? code review of the engine
   while fmt-5000 is down? something else?) rather than guessing.
2. If asked to keep making engine/lazy-strategy code changes: do them on a
   feature branch off `incoming`, open a PR, get it reviewed/merged the same
   way every prior feature landed (see §2) — the user has been consistent
   about "big feature → branch → PR → merge into incoming," not direct
   commits to `incoming`. The last commit (`3b4330f`, CLI-flag refactor) *was*
   committed directly to `incoming` though, so small/mechanical cleanups seem
   to be treated as an exception — use judgement, ask if unsure.
3. Do **not** push anything from `looping_experiment/` (it isn't tracked
   here anyway, but if it ever gets created on this Mac by mistake, don't
   `git add` it) and don't merge `incoming` into `main` without the user's
   explicit go-ahead (no PR for that merge exists/was requested yet).
4. When fmt-5000 returns, work through the checklist in §6 before resuming
   oracle-improvement experiments.
