# WIP: Lazy Sequence Materialization Debug/Handover

## Scope
This note documents the current investigation around **lazy sequence progress** in serve-mode Start tab, specifically why `vi_sweep_masks` can appear stuck in `persisting/pending` without clear per-item materialization.

## Context
The workflow used for reproduction is:

```imgql
import "simpleitk"

dataset_root = "tests/data/datasets/BraTS_2019_HGG"
k = 40
hi_thr = 1
vi_thr_start = 80
vi_thr_stop = 90
vi_ticks = range(vi_thr_start, vi_thr_stop)
to_thr(tick) = tick / 100
vi_thresholds = map(to_thr, vi_ticks)
all_flair_paths = dir(dataset_root, "*_flair.nii.gz", true, true)
flair_paths = subsequence(all_flair_paths, 30, 30+k)

read_image(path) = ReadImage(path)
to_intensity(img) = intensity(img)

preprocess_flair(flair) =
  let background = touch(leq_sv(0.1, flair), border) in
  let brain = not(background) in
  percentiles(flair, brain, 0)

let bts(hi_thr,vi_thr) =
  let hyper_intense = smoothen(geq_sv(hi_thr, pflair), 5.0) in
  let very_intense = smoothen(geq_sv(vi_thr, pflair), 2.0) in
       grow(hyper_intense, very_intense)

sweep_case(pflair) =
  for vi_thr in vi_thresholds do
     bts(hi_thr,vi_thr)

flair_images = map(read_image, flair_paths)
flair_intensities = map(to_intensity, flair_images)
pflair_images = map(preprocess_flair, flair_intensities)
vi_sweep_masks = map(sweep_case, pflair_images)
```

## Already Committed UI Work
Recent commits related to lazy/progressive visualization:
- `c2b3e26` Tone down interaction motion to remove zoom artifacts
- `7724d56` Enable progressive lazy sequence materialization with per-item status

UI files involved:
- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
- `implementation/ui/src/lib/components/tabs/StartValueCanvas.svelte`
- `implementation/ui/src/lib/components/tabs/StartTab.test.js`
- `implementation/ui/src/app.css`

## API Reproduction (live backend)
Backend detected at `127.0.0.1:8000`.

### 1) Symbol introspection succeeds
`POST /api/v1/playground/symbols` reports:
- `available=true`
- `diagnostics=[]`
- `symbol_table.vi_sweep_masks` resolves to a node id.

### 2) Value paging reports pending/persisting with no per-item materialization
Repeated `POST /api/v1/playground/value/page` for `vi_sweep_masks` (`offset=0, limit=18, enqueue=true`) shows:
- `compute_status` transitions `queued -> persisting`
- page items remain `status=pending`
- repeated re-enqueues can occur after persistence timeout heuristic in main endpoint.

### 3) Job payload confirms computation completed but persistence remains pending
`GET /api/v1/playground/jobs/{job_id}` for value-resolve jobs shows:
- `status=completed`
- no execution errors
- goal metadata contains `persisted: "pending"`

## Root Cause (current analysis)

### A) Value-resolve persistence flush timeout default is zero
File: `implementation/python/voxlogica/features.py`
- `_DEFAULT_VALUE_RESOLVE_PERSIST_TIMEOUT_S = 0.0`
- `_resolve_persistence_flush_timeout_s()` returns this default for `job_kind == "value-resolve"` unless env override exists.
- Effect: value-resolve job returns before persistence queue drains.

### B) Sequence persistence is eager and potentially very expensive
File: `implementation/python/voxlogica/storage.py`
- `SQLiteResultsDatabase.put_success()` routes sequence values to `_persist_sequence_with_refs_locked()`.
- `_persist_sequence_with_refs_locked()` iterates sequence values and persists child refs/items for the entire sequence (streaming or paging path).
- For sequence-of-sequences/image pipelines this can be very heavy and keep metadata in `persisted="pending"` for a long time.

### C) UI progression depends on `/playground/value/page` item statuses
Files:
- `implementation/python/voxlogica/main.py` (`/playground/value`, `/playground/value/page`)
- `implementation/ui/src/lib/components/tabs/StartValueCanvas.svelte`

If store child items are not materialized yet, page entries stay pending; user perceives stall.

## Relevant Backend Hotspots

### Value endpoint orchestration
File: `implementation/python/voxlogica/main.py`
- `playground_value_endpoint()`:
  - tracks store hit/miss
  - tracks existing value-jobs
  - returns pending/persisting payloads with descriptors
  - can re-enqueue when persistence appears stalled.

### Page endpoint orchestration
File: `implementation/python/voxlogica/main.py`
- `playground_value_page_endpoint()`:
  - calls value endpoint first
  - for pending/persisting, uses `_transient_sequence_page_from_store()` to synthesize page status from hashed child nodes.

### Store paging
File: `implementation/python/voxlogica/serve_support.py`
- `inspect_store_result_page()` for materialized sequence relies on persisted `result_pages` rows.
- If root is materialized with sparse/empty pages, UI can end up with empty page presentation (`0-0` style behavior).

## Current Gap
The system still lacks an explicit **path-focused lazy compute contract** that guarantees:
1. click item path (`/i`) can compute/persist just that item promptly,
2. without waiting for full root sequence persistence.

## Proposed Next Step (implementation direction)

### 1) Add path-focused value materialization hook
Likely files:
- `implementation/python/voxlogica/main.py`
- `implementation/python/voxlogica/features.py`

Idea:
- For non-root `path` requests (`/i`, `/i/j`), pass `_goal_path` context into value-resolve job payload.
- In `handle_run()` (serve value-resolve path), after root goal evaluation, resolve only requested subpath and persist/fill that focused node in the shared materialization store.
- This enables item-level materialization without requiring full root persistence.

### 2) Reduce root sequence persistence pressure for value-resolve
Likely file:
- `implementation/python/voxlogica/features.py`

Potential options:
- raise default value-resolve flush timeout modestly, or
- switch path-focused jobs to a no-cache execution path and only persist focused result(s), avoiding eager root sequence write-through.

### 3) Add fallback when materialized sequence page is empty but length/status imply pending children
Likely files:
- `implementation/python/voxlogica/main.py`
- `implementation/python/voxlogica/serve_support.py`

Goal:
- avoid blocked-looking empty page when descriptor exists but child pages are not yet persisted.

## Validation Commands Used
Example API checks used during investigation:

```bash
# symbols
jq -Rs '{program: .}' /tmp/vi_sweep_test.imgql \
  | curl -sS -X POST http://127.0.0.1:8000/api/v1/playground/symbols \
    -H 'content-type: application/json' -d @-

# paged value
jq -Rs '{program: ., variable:"vi_sweep_masks", offset:0, limit:18, enqueue:true}' /tmp/vi_sweep_test.imgql \
  | curl -sS -X POST http://127.0.0.1:8000/api/v1/playground/value/page \
    -H 'content-type: application/json' -d @-

# job status
curl -sS http://127.0.0.1:8000/api/v1/playground/jobs/<job_id>
```

## Status
- Investigation complete and reproducible.
- Root cause strongly linked to eager sequence persistence + zero flush timeout in value-resolve path.
- Next agent can continue directly from backend files listed above.

## Update: Runtime Preview Bridge (Modular, Type-Agnostic)

Implemented a runtime-preview bridge so `/playground/value` and `/playground/value/page` can return usable payloads while durable store persistence is still pending.

### What changed

- `implementation/python/voxlogica/serve_support.py`
  - Added `inspect_runtime_value_page(...)`.
  - This inspects pageable **in-memory runtime values** (sequence or mapping) and emits the same page contract used by store paging.

- `implementation/python/voxlogica/features.py`
  - Added `_build_runtime_previews_for_goal(...)`.
  - For serve-mode goal descriptors, feature execution now captures JSON-safe runtime previews keyed by path (root and focused path), including:
    - canonical descriptor
    - optional first page for pageable types
    - optional JSON value for scalar-like paths
  - Preview extraction is generic via `adapt_runtime_value` + `resolve(path)`, so it is not sequence-specific by design.

- `implementation/python/voxlogica/main.py`
  - Added runtime preview extraction from completed value jobs.
  - In `job status=completed` + `persisted=pending` branch, if runtime preview for requested path exists:
    - return `materialization="computed"`
    - keep `compute_status="persisting"` and `store_status="missing"`
    - include descriptor (and value/page when available)
  - `playground_value_page_endpoint(...)` now uses runtime preview page before falling back to transient store synthesis.
  - Added robust path normalization for preview lookup (`""` vs `"/"`).

### Why this is modular

- Preview generation and preview consumption are separated.
- Payloads use canonical descriptor/page contracts already used by store responses.
- Runtime preview works for any type representable by `adapt_runtime_value` and for any path resolvable via `resolve(path)`.
- Sequence-specific logic remains only where hashing/store reference synthesis is required; preview layer itself is type-agnostic.

### Tests added

- `tests/unit/test_main_entrypoints.py`
  - Added `test_playground_value_uses_runtime_preview_while_persisting` to validate:
    - page retrieval from runtime preview while persistence is pending
    - nested value retrieval from runtime preview while persistence is pending

### Validation

- `.venv/bin/python -m pytest tests/unit/test_main_entrypoints.py -k 'runtime_preview_while_persisting or paging_works_while_sequence_is_persisting or completed_pending_persistence_status'`
  - Passed.

## Update: Background Fill Scheduler + Interactive Preemption

This section documents the new serve-side execution contract for background computation.

### Backend queue model

File: `implementation/python/voxlogica/serve_support.py`

- `PlaygroundJob` now exposes `priority_class` (`interactive`, `background`, `normal`) in public payloads.
- `PlaygroundJobManager` now has:
  - `_background_queue` (`deque[str]`)
  - `_background_active_job_id`
  - priority classification helper `_priority_class_for_payload(...)`
- Background jobs (`_job_kind == "background-fill"` or `_background_fill == true`) are **queued** and only dispatched when no interactive value-resolve is active.
- Interactive value jobs (`_job_kind == "value-resolve"`) trigger preemption via `_preempt_background_for_interactive_locked()`:
  - active background process is terminated
  - job state returns to `queued`
  - `metrics.preemptions` increments
  - background job is requeued for later resume.

## Update: Inspectable Sequence Semantics Trace (2026-03-08)

This section records the findings behind the next architectural change: **sequences must become inspectable before full computation/persistence**.

### Current execution model

Reducer and runtime files inspected:

- `implementation/python/voxlogica/reducer.py`
- `implementation/python/voxlogica/execution_strategy/strict.py`
- `implementation/python/voxlogica/execution_strategy/dask.py`
- `implementation/python/voxlogica/execution_strategy/results.py`

Findings:

1. `map(f, xs)` and `for x in xs do body` already converge semantically:
   - both lower to one sequence-producing node plus one closure node
   - there is **no per-item node fanout** in the symbolic plan
2. `StrictExecutionStrategy._evaluate_map(...)` returns a plain `SequenceValue`
3. `StrictExecutionStrategy._evaluate_runtime_expression(EFor)` also returns a plain `SequenceValue`
4. `DaskExecutionStrategy._evaluate_map(...)` either:
   - keeps a `dask.bag`
   - or falls back to the strict iterator path

Consequence:
- planner/IR currently treats a sequence as one opaque runtime artifact
- per-item materialization cannot come from plan nodes because those nodes do not exist yet

### Current per-item identity model

Files inspected:

- `implementation/python/voxlogica/lazy/hash.py`
- `implementation/python/voxlogica/storage.py`
- `implementation/python/voxlogica/value_model.py`

Findings:

1. Before persistence, child identity is effectively **path-based only** (`/0`, `/0/3`, ...)
2. Deterministic hashed child ids are introduced by persistence:
   - `hash_sequence_item(parent_node_id, index)`
   - used inside `SQLiteResultsDatabase._persist_sequence_child_locked(...)`
3. `adapt_runtime_value(...)` currently wraps runtime iterators as `VoxIteratorSequenceValue`, which:
   - pages by iterating raw values
   - knows nothing about child state / blocked-on dependencies / running items

Consequence:
- UI page inspection has no first-class live child state model
- store references work only after persistence

### Current serve-mode bottlenecks

Files inspected:

- `implementation/python/voxlogica/main.py`
- `implementation/python/voxlogica/serve_support.py`
- `implementation/python/voxlogica/features.py`
- `implementation/python/voxlogica/storage.py`

Findings:

1. `StrictExecutionStrategy._evaluate_node(...)` always calls `prepared.materialization_store.put(...)`
2. `MaterializationStore.put(...)` eagerly enqueues persistence for any serializable value
3. For sequences, `SQLiteResultsDatabase.put_success(...)` routes to `_persist_sequence_with_refs_locked(...)`, which walks the entire sequence
4. `/ws/playground/value` still uses a timeout loop (`asyncio.wait_for(..., timeout=0.8)`) and compares payload hashes
5. Start tab still has timer-based page/path polling (`pendingPoll`, `scheduleRecordPagePoll`, path/record poll timers)
6. `PlaygroundJobManager.inspect_value_job_runtime(...)` only exposes runtime previews when the whole value job is already `completed`

Consequence:
- selecting a sequence variable still couples “inspect container” to “compute/persist full sequence”
- UI polling obscures the real backend state
- nested sequences cannot materialize one-by-one during the running phase

### Target implementation shape

The next implementation slice should introduce:

1. a first-class **inspectable sequence runtime** above `SequenceValue`
2. a **child scheduler** with priorities:
   - click/focused child
   - visible-page warmup
   - background fill
3. per-item live state:
   - `not_loaded`
   - `queued`
   - `blocked`
   - `running`
   - `persisting`
   - `ready`
   - `failed`
4. page snapshots from runtime while the parent container is still active
5. websocket page subscriptions driven by runtime/job change notifications instead of timeout polling

### Dynamic DAG fanout: considered alternative

This was explicitly considered and should be preserved for handover.

Question raised:

- should `map` / `for ... do ...` dynamically inject one computation node per sequence item into the DAG?

Current conclusion:

- **not in the reducer / symbolic plan layer**
- keep parser and reducer semantics stable:
  - one container node for the sequence-producing operator
  - one closure/function node
- introduce per-item child refs/tasks at **runtime**, not as eagerly expanded symbolic nodes

Rationale:

1. The current language semantics already make `map` and `for ... do ...` equivalent sequence constructors.
2. Expanding a symbolic node per item in the reducer would:
   - destroy laziness for large or infinite sequences
   - make plan size depend on runtime cardinality
   - require cardinality knowledge too early
3. The UI requirement is not “more static nodes”, but:
   - inspect the container immediately
   - expose child items and their state before the whole sequence is computed
   - materialize items independently
4. That is better modeled as:
   - a stable parent sequence node in the plan
   - deterministic runtime child refs/tasks (`parent node id + child token/index`)
   - runtime scheduling and persistence per child

So the intended semantics change is:

- `map` and `for ... do ...` remain equivalent at the language surface
- both produce a first-class **inspectable sequence container**
- the container can spawn and track child computations dynamically at runtime
- this is runtime task fanout, **not** reducer-time DAG explosion

If a later distributed scheduler wants first-class item-level task identities, those should be derived from:

- parent node id
- child family
- child token/index

and surfaced consistently in API/UI payloads, without changing source-level syntax.

### Additional design decisions not to lose

The following points were discussed/decided during analysis and should remain explicit for future agents.

#### 1) Root/container phase vs child/item phase

Sequence-producing evaluation should be split conceptually into two phases:

1. **container phase**
   - establish that a node is an inspectable sequence
   - expose descriptor, optional length hint, and child addressing rules
2. **item phase**
   - compute child values independently and incrementally

This is important because the current implementation conflates:

- “sequence container exists”
- “all sequence items are persisted”

Those need to become separate states.

#### 2) Child-state vocabulary should replace ambiguous `waiting`

The UI should stop using `waiting` as a catch-all for nested values.

Target per-item state vocabulary:

- `not_loaded`
- `queued`
- `blocked`
- `running`
- `persisting`
- `ready`
- `failed`

Important semantic distinction:

- `blocked` means “this child is waiting on upstream dependency”
- `queued` means “this child has been requested/scheduled but not started”
- `not_loaded` means “visible container slot exists but this child has not yet been explicitly requested or warmed”

#### 3) `map(f, g(x))[i]` should depend on upstream child `i`, not the whole upstream sequence

This is a semantic requirement, not just a UI requirement.

For transformed sequences:

- child `i` of `map(f, xs)` should depend on child `i` of `xs`
- child `i` should not wait for full completion of `xs`

Equivalent rule for `for_loop`:

- the loop body instantiated at item `i` depends only on input item `i`

This is what allows:

- outer sequences to become inspectable immediately
- nested sequences to materialize one-by-one

#### 4) Page visibility should drive low-priority warmup, not forced eager computation

When a collection page is visible:

- visible items may be enqueued at low priority
- a clicked item must outrank warmup work
- background fill must be lower priority than visible-page warmup

This is the intended priority order:

- focused click / selected path
- visible page warmup
- background fill

We explicitly did **not** assume preemptive kernel interruption; priority is primarily:

- queue admission order
- dispatch order

#### 5) Ready child values should be served from memory before persistence completes

This is broader than the already implemented runtime preview bridge.

The durable requirement is:

- if child `[i]` has already completed in memory, reopening `[i]` should be immediate
- this must hold even if parent/root persistence is still ongoing
- this should work for nested paths (`/6/1`, etc.), not just top-level children

So the runtime child cache must be path-aware and not root-only.

#### 6) Websocket contract should become page-aware, not only focused-value-aware

Current websocket shape is still focused-value polling.

Desired change:

- page subscriptions should include:
  - `program`
  - `node_id` or `variable`
  - `path`
  - `offset`
  - `limit`
- backend should push page deltas or fresh page snapshots on actual child-state changes

The page websocket is the core mechanism for:

- progressive outer sequence materialization
- progressive nested sequence materialization
- removal of UI polling flicker

#### 7) Sequence persistence should become incremental

The persistence model should change from:

- persist entire sequence root/pages as one long traversal

to:

- persist ready child items as they complete
- make child refs visible early
- allow parent/root descriptor to exist before full sequence traversal completes

This is necessary so cached child values appear instantly for large sequences.

#### 8) Initial migration scope should stay narrow

The first inspectable runtime implementations should target only the sequence producers needed by the current workflows:

- `range`
- `dir`
- `subsequence`
- `map`
- `for_loop`

Other sequence-producing mechanisms can keep the current opaque fallback until the runtime contract is proven.

## Step 1 completed: inspectable runtime contract foundations

Files added/changed:

- `implementation/python/voxlogica/inspectable_sequence.py`
- `implementation/python/voxlogica/lazy/hash.py`
- `implementation/python/voxlogica/value_model.py`
- `tests/unit/test_inspectable_sequence.py`

What this step implements:

1. A first-class runtime contract for inspectable sequences:
   - `length_hint()`
   - `child_ref(index)`
   - `peek_item(index)`
   - `ensure_item(index, priority=...)`
   - `resolve_item(index, priority=...)`
   - `page_snapshot(offset, limit, priority=...)`
2. Foundational runtime implementations:
   - `InspectableRangeSequence`
   - `InspectableListSequence`
   - `InspectableIteratorSequence`
   - `InspectableMappedSequence`
   - `InspectableSubsequence`
3. A generic deterministic child-ref hash helper:
   - `hash_child_ref(parent_node_id, family=..., token=...)`
   - `hash_sequence_item(...)` now delegates to it as compatibility wrapper
4. `VoxIteratorSequenceValue` can now exploit:
   - `length_hint()`
   - `page_snapshot(...)`
   when present on the underlying runtime sequence

What this step does **not** yet implement:

- no backend child scheduler yet
- no live page websocket yet
- no UI status model changes yet
- no integration into `StrictExecutionStrategy` / `DaskExecutionStrategy` yet

Why this step matters:

- it establishes the runtime abstraction needed for incremental inspection
- it provides deterministic child refs before persistence is involved
- it keeps the next steps small: execution integration, then serve/API, then UI

Validation for this step:

```bash
python -m py_compile implementation/python/voxlogica/inspectable_sequence.py implementation/python/voxlogica/lazy/hash.py implementation/python/voxlogica/value_model.py
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py tests/unit/test_hash_determinism.py tests/unit/test_hash_properties.py -q
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py --cov=voxlogica.inspectable_sequence --cov=voxlogica.lazy.hash --cov=voxlogica.value_model --cov-report=term-missing -q
```

### Immediate implementation boundary

The first migrated sequence producers should be:

- `range`
- `dir`
- `subsequence`
- `map`
- `for_loop`

The language surface should remain unchanged:

- no parser changes
- no user-level reimplementation of `map` over `for`
- reducer remains one container node + closure node, with inspectability handled in runtime semantics

### API contract for full background materialization

File: `implementation/python/voxlogica/main.py`

- `RunRequest` now supports `background_fill: bool = false`.
- `POST /api/v1/playground/jobs` behavior:
  - default request: unchanged (`run` semantics)
  - with `background_fill=true`:
    - `_job_kind = "background-fill"`
    - `_background_fill = true`
    - `_include_goal_descriptors = true`
    - `_goals` is set to all declaration node ids resolved from `_program_introspection(...)`.

Helper:
- `_background_fill_goal_ids(program_text)` derives stable declaration goals from symbol bindings.

### UI request shape

File: `implementation/ui/src/lib/api/client.js`

- `createPlaygroundJob(program, { backgroundFill = true } = {})` now sends:
  - `background_fill` in POST body
  - default is `true` for run-triggered background materialization behavior.

### Tests

File: `tests/unit/test_main_entrypoints.py`

- Added `test_playground_job_background_fill_payload`:
  - validates that background-fill requests map to:
    - `_job_kind = "background-fill"`
    - `_background_fill = true`
    - `_include_goal_descriptors = true`
    - non-empty `_goals`.

### Validation notes

- `python -m py_compile implementation/python/voxlogica/main.py implementation/python/voxlogica/serve_support.py` passes.
- Targeted new test passes:
  - `.venv/bin/python -m pytest tests/unit/test_main_entrypoints.py -k playground_job_background_fill_payload`
- Full `test_main_entrypoints.py` currently has a pre-existing unrelated failure in `test_list_primitives_and_repl_and_serve` (bind permission in sandbox, and an existing `integer` vs `number` expectation mismatch in another path).
- `npm --prefix implementation/ui run test`
  - Passed.
- `npm --prefix implementation/ui run build`
  - Passed.
