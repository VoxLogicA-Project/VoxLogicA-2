# WIP: Lazy Sequence Materialization Debug/Handover

## Update: Main Start Tab Needs A True Ongoing-Operations Feed

Current bug/UX gap:

- the dedicated Compute Log tab receives some HTTP/cache/ws events
- but the Start tab still hides most of the actual resolve lifecycle in `console.info`
- result: the user cannot tell whether the UI is:
  - resolving a root value
  - waiting on a websocket subscription
  - polling a pending value
  - loading a nested page
  - loading a nested child path

Planned slice in progress:

- upgrade `implementation/ui/src/lib/stores/computeActivity.js` from a flat history-only store to:
  - recent history
  - live/ongoing operations derived from lifecycle events
- route `implementation/ui/src/lib/components/tabs/StartTab.svelte` resolve/page/path/socket lifecycle into that store
- expose the feed inline in the Start tab behind a compact toggle button near the main action row
- keep the existing lazy/progressive engine untouched; this slice is UI observability only

Validation plan:

- add store-level tests for start/update/finish operation tracking
- add Start-tab UI regression coverage for the inline operations panel
- run the existing Start-tab tests and rebuild the static serve bundle

### Implemented

Files:
- `implementation/ui/src/lib/stores/computeActivity.js`
- `implementation/ui/src/lib/stores/computeActivity.test.js`
- `implementation/ui/src/lib/api/client.js`
- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
- `implementation/ui/src/lib/components/tabs/StartTab.test.js`
- `implementation/ui/src/lib/components/tabs/ComputeLogTab.svelte`
- `implementation/ui/src/app.css`

What changed:
- `computeActivity` now keeps:
  - recent history
  - `ongoingComputeActivity` for live operations
- the Start tab now publishes its real lifecycle to the activity store instead of only `console.info`:
  - root resolve start/update/finish
  - pending poll start/update/finish
  - nested path load start/update/finish
  - nested page load start/update/finish
  - websocket watch start/update/finish for root values and record pages
- the Start tab now exposes a compact inline **Operations** toggle in the main action row
  - when opened, it shows:
    - `Live now`
    - `Recent`
- the dedicated Compute Log tab also shows the live operations section first

Validation:
- `npm --prefix implementation/ui run test -- src/lib/stores/computeActivity.test.js`
- `npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js`
- `npm --prefix implementation/ui run test -- src/lib/api/client.test.js`
- `npm --prefix implementation/ui run build`

## Update: Nested Inspectable Sequence Persistence Was Corrupting Overlay Trees

New proven bug in the `vi_sweep_overlays` path:

- outer sequence values could become `ready`
- nested page inspection `/0` could expose overlay descriptors from runtime
- but the viewer render URL `/api/v1/results/store/<root>/render/nii?path=/0/0/0` returned `404`

### Root cause

Files:
- `implementation/python/voxlogica/storage.py`
- `implementation/python/voxlogica/pod_codec.py`
- `implementation/python/voxlogica/inspectable_sequence.py`

Problem:
- `_persist_sequence_child_locked(...)` persisted child values through `encode_for_storage(...)`
- when the child value was itself an `InspectableSequenceValue`, `adapt_runtime_value(...)` downgraded it to `VoxIteratorSequenceValue`
- generic sequence encoding in `pod_codec._encode_sequence_pages(...)` uses `sequence.page(...)`
- inspectable `page(...)` only returns already-ready child values, and for a fresh nested sequence that can be empty even though the sequence exists
- result: nested child sequence records were written as bogus
  `{"encoding":"sequence-pages-v1","length":0,...}`
- root page items still pointed at those child node ids, so deep render path resolution descended into empty child records and the layer render endpoint returned `404`

### Proof

Reproduced against the overlay workflow on a clean server using a fresh results DB:

- before fix:
  - root `vi_sweep_overlays` stored as `sequence-node-refs-v1`
  - child `/0` store record existed but was `sequence-pages-v1` with `length=0`
  - overlay child record for `/0/0` did not exist
  - `GET /api/v1/results/store/<root>/render/nii?path=/0/0/0` returned `404`
- after fix:
  - child `/0` store record is `sequence-node-refs-v1` with `length=9`
  - overlay child record exists and is persisted as `overlay`
  - the same render URL returns `200` with NIfTI payload

Important operational note:
- old corrupted entries remain in an existing `results.db`
- a clean DB or recomputation is required to observe the fix on previously cached node ids

### Implemented fix

File:
- `implementation/python/voxlogica/storage.py`

Change:
- `_persist_sequence_child_locked(...)` now detects when the child value adapts to `VoxSequenceValue`
- instead of passing it through `encode_for_storage(...)`, it recursively persists that child sequence with `_persist_sequence_with_refs_locked(...)`
- non-sequence children still use the normal `encode_for_storage(...)` path

Effect:
- nested inspectable sequences preserve child refs and child pages
- sequence-of-sequences-of-overlays stays navigable in durable storage
- root path render URLs can resolve down to persisted overlay layers correctly

### Regression test

File:
- `tests/unit/test_serve_support.py`

Coverage:
- persist `InspectableListSequence([InspectableListSequence([overlay])])`
- assert child sequence record is `sequence-node-refs-v1` with `length=1`
- assert child page exposes the overlay item
- assert `render_store_result_nifti_gz(..., path="/0/0/0")` succeeds

## Update: Inspectable Child Paths Must Preserve Progress State

New bug found after inspectable per-item scheduling landed:

- direct path inspection for inspectable sequence children (for example `/5`) could raise
  `Sequence index out of range in path '/5'`
- even when neighboring items existed and the child was merely `queued`, `running`, `blocked`, or `persisting`

### Root cause

Files:
- `implementation/python/voxlogica/value_model.py`
- `implementation/python/voxlogica/serve_support.py`
- `implementation/python/voxlogica/main.py`

Problem:
- generic runtime child-path resolution still used `adapt_runtime_value(...).resolve(path=...)`
- for `InspectableSequenceValue`, the compatibility wrapper `VoxIteratorSequenceValue._iter_window(...)` only returns items whose snapshot state is `ready`
- `VoxSequenceValue.resolve(...)` interprets an empty one-item window as **index out of range**
- therefore “item exists but is not ready yet” collapsed into a false terminal failure

### Correct behavior

- inspectable child paths must preserve runtime progress semantics:
  - `not_loaded`
  - `queued`
  - `blocked`
  - `running`
  - `persisting`
  - `ready`
  - `failed`
- direct path inspection should only report out-of-range when the inspectable sequence itself proves the index is absent

### Implemented fix

- added a dedicated runtime path resolver in `implementation/python/voxlogica/serve_support.py`
  - `_resolve_runtime_value_or_progress(...)`
- it walks inspectable sequence paths token-by-token
- when a child snapshot is not ready, it returns a progress payload instead of forcing generic sequence resolution
- updated:
  - `describe_runtime_value(...)`
  - `build_runtime_preview(...)`
  - `RuntimeValueInspector.wait_for_change(...)`
  - `LiveRuntimeValueInspector.wait_for_change(...)`
  - `inspect_runtime_value_page(...)`
- updated `implementation/python/voxlogica/main.py`
  - `_payload_from_runtime_preview(...)` now preserves preview status instead of hard-coding runtime previews as `computed`

### Regression tests

- `tests/unit/test_serve_support.py`
  - blocked inspectable child path stays blocked
  - runtime inspector preview does not fabricate out-of-range for blocked child
- `tests/unit/test_main_entrypoints.py`
  - `/api/v1/playground/value` keeps a blocked runtime child as `materialization=pending`, `compute_status=blocked`

## Update: Nested Pending Child Paths Must Not Inherit Root Sequence Type

New viewer bug found with `vi_sweep_overlays`:

- the real semantic shape is `sequence(case -> sequence(threshold -> overlay))`
- but unresolved nested child paths like `/0/0` could be reported as another `sequence`
- the UI then opened a fake third collection level, which looked like `seq -> seq -> seq`

### Root cause

File:
- `implementation/python/voxlogica/main.py`

Problem:
- `_in_progress_descriptor(...)` used the **root node output kind** for every path
- for a root sequence variable, unresolved nested child paths inherited `vox_type="sequence"`
- this was only a placeholder, but the UI treated it as a real collection descriptor

### Correct behavior

- root unresolved value may still use root output kind for the top-level placeholder
- nested unresolved child paths must stay `unavailable` unless runtime/store inspection proves they are pageable

### Implemented fix

- `implementation/python/voxlogica/main.py`
  - `_in_progress_descriptor(...)` now returns `_pending_descriptor(...)` for non-root paths
  - only root path placeholders use the symbolic output kind

### Regression test

- `tests/unit/test_main_entrypoints.py`
  - unresolved nested child path `/0/0` under a running root sequence must return `vox_type="unavailable"`
  - it must not inherit the root `sequence` type

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

## Step 1 Completed: Inspectable Sequence Foundations

Commit: `deee772` (`runtime: add inspectable sequence foundations`)

This step introduced the runtime-side data model for inspectable sequences without yet changing executor semantics.

Files:
- `implementation/python/voxlogica/inspectable_sequence.py`
- `implementation/python/voxlogica/lazy/hash.py`
- `implementation/python/voxlogica/value_model.py`
- `tests/unit/test_inspectable_sequence.py`

Key points:
- Added first-class inspectable containers with deterministic child refs.
- Added generic `hash_child_ref(parent, family, token)`.
- Kept `hash_sequence_item(parent, index)` as a compatibility wrapper.
- Added per-item page snapshots and on-demand `ensure_item`/`resolve_item`.
- Added adapters so existing sequence wrappers can consume `page_snapshot`.

## Step 4 In Progress: Exact UI Item States

Problem:
- backend page payloads now carry exact per-item states (`not_loaded`, `queued`, `blocked`, `running`, `persisting`, `ready`, `failed`)
- Start UI still collapses those into old vague labels (`waiting`, `pending`, `materialized`, `upstream`)
- result: the feature is technically advancing while the user still cannot tell what a visible collection item is doing

Files to change:
- `implementation/ui/src/lib/components/tabs/StartValueCanvas.svelte`
- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
- `implementation/ui/src/app.css`
- `implementation/ui/src/lib/components/tabs/StartTab.test.js`

Planned slice:
1. Make collection cards consume backend `item.state` directly when present.
2. Keep backward compatibility with older `item.status` payloads by normalizing:
   - `materialized/cached/computed/completed -> ready`
   - `pending/missing -> not_loaded`
3. Stop using UI-only state labels `waiting` and `upstream`.
4. Update the page polling heuristic to look for the new exact states instead of `unavailable` descriptors.
5. Add focused tests proving:
   - visible items render `blocked`, `queued`, `running`, `persisting`, `ready`, `failed`
   - ready nested items stay clickable
   - pending pages continue polling while any visible item remains non-terminal

Non-goal for this slice:
- no websocket protocol changes yet
- no child-task scheduler yet

### Step 4 completed

Commit:
- pending commit after validation of this slice

Implemented:
- `StartValueCanvas.svelte` now consumes the backend item-state contract directly.
- Legacy page payloads remain compatible through normalization:
  - `materialized/computed/completed/cached -> ready`
  - `pending/missing -> not_loaded`
- Removed UI-only visible item labels `waiting` and `upstream` from collection rows.
- Collection item tooltips now include `blocked_on`, `state_reason`, and item-level `error` when provided.
- `StartTab.svelte` page polling heuristic now keys off exact item states instead of inferred `unavailable` descriptors.

Validation:
- `npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js`
- `npm --prefix implementation/ui run build`

Focused tests added:
- exact item states render as `queued`, `blocked`, `ready`
- blocked item tooltip surfaces upstream dependency
- page polling still continues while visible items are non-terminal

Remaining gap after step 4:
- page updates are still timer-driven from the client side
- websocket transport is still focused on value-path updates, not page subscriptions
- per-item states are now visible, but they are not yet pushed live from the backend

## Step 5 Completed: Page-Aware Value Websocket

Commit:
- pending commit after validation of this slice

Implemented:
- `/ws/playground/value` now accepts `mode: "page"` subscriptions in addition to focused value subscriptions.
- Page websocket subscriptions carry `variable`, `path`, `offset`, `limit`, and preserve first-tick `enqueue=true` semantics.
- Start tab now treats page websocket updates as the primary collection refresh path when `WebSocket` is available.
- Existing page timer polling remains only as fallback:
  - test mode
  - environments without `WebSocket`
  - request failure/timeout fallback paths
- Page failure behavior still preserves the collection shell by synthesizing a fallback page instead of collapsing the viewer.

Files changed in this slice:
- `implementation/python/voxlogica/main.py`
- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
- `tests/unit/test_main_entrypoints.py`

Validation:
- `python -m py_compile implementation/python/voxlogica/main.py`
- `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_main_entrypoints.py -q -k 'playground_value_websocket_supports_page_subscriptions'`
- `npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js`
- `npm --prefix implementation/ui run build`

Focused tests added:
- page websocket subscribe/stream/terminal contract for `/ws/playground/value`
- UI regression coverage retained for collection failure preservation and exact item states

Remaining gap after step 5:
- backend websocket loop still snapshots on a timed cadence rather than reacting to child-state events
- path-level child value subscriptions still use the older path polling fallback
- scheduler-level child task prioritization is still separate from live page subscriptions

Important design note:
- This step does **not** explode the symbolic DAG.
- The reducer still emits one sequence-producing node for `map`/`for`.
- Dynamic child identity is introduced at runtime, not during symbolic planning.

Validation:
- `python -m py_compile implementation/python/voxlogica/inspectable_sequence.py implementation/python/voxlogica/lazy/hash.py implementation/python/voxlogica/value_model.py`
- `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py tests/unit/test_hash_determinism.py tests/unit/test_hash_properties.py -q`

## Step 2 Completed: Strict Executor Integration

This step wires inspectable sequences into the executor layer while deliberately keeping the scope limited:
- strict execution is upgraded to return inspectable containers for core sequence forms
- Dask is patched for compatibility only
- no scheduler/page websocket/live child-state push yet

Files:
- `implementation/python/voxlogica/execution_strategy/strict.py`
- `implementation/python/voxlogica/execution_strategy/dask.py`
- `implementation/python/voxlogica/execution_strategy/__init__.py`
- `implementation/python/voxlogica/inspectable_sequence.py`
- `tests/unit/test_execution_trace.py`

What changed:
- `StrictExecutionStrategy` now returns inspectable containers for:
  - `range`
  - `map`
  - `for_loop`
  - sequence-like `load`
- Runtime closures/functions now propagate a `__vox_runtime_ref__`.
- Nested mapped items derive stable per-item runtime refs, so nested inspectable sequences do not all share one parent identity.
- Runtime `for ... do ...` expressions now build an inspectable mapped container instead of an opaque iterator-only `SequenceValue`.
- `_coerce_sequence(...)` now upgrades sequence-like runtime values into inspectable containers.
- `execution_strategy.__init__` now lazily exposes strict/dask strategy classes to avoid circular import through `inspectable_sequence -> results -> package __init__`.
- `DaskExecutionStrategy` now accepts the widened strict helper signatures and preserves runtime-closure fallback.

Tests added in this step:
- strict range goal returns `InspectableRangeSequence`
- strict `map(range, range(...))` yields nested inspectable sequences with distinct child refs
- strict `for ... do ...` matches `map` semantics at runtime
- Dask runtime-closure path still works after the signature changes

Validation:
- `python -m py_compile implementation/python/voxlogica/inspectable_sequence.py implementation/python/voxlogica/execution_strategy/strict.py implementation/python/voxlogica/execution_strategy/dask.py tests/unit/test_execution_trace.py`
- `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_execution_trace.py tests/unit/test_inspectable_sequence.py tests/unit/test_hash_determinism.py tests/unit/test_hash_properties.py -q`
- `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_execution_trace.py tests/unit/test_inspectable_sequence.py --cov=voxlogica.execution_strategy.strict --cov=voxlogica.execution_strategy.dask --cov=voxlogica.inspectable_sequence --cov=voxlogica.lazy.hash --cov-report=term-missing -q`

Focused coverage after step 2:
- `voxlogica.execution_strategy.strict`: 57%
- `voxlogica.execution_strategy.dask`: 53%
- `voxlogica.inspectable_sequence`: 84%
- `voxlogica.lazy.hash`: 93%
- total across targeted step-2 modules: 67%

What this step still does **not** solve:
- visible sequence pages do not yet receive live per-item status updates during a running job
- child items are not yet independently scheduled/persisted while the parent job is still active
- websocket/page subscriptions are still not page-aware

Planned next step:
- introduce a serve/runtime child-state registry and page-aware page snapshots so visible items can move through `not_loaded / queued / blocked / running / persisting / ready / failed` without waiting for whole-parent completion

## Step 3 Completed: Live Runtime Inspection for Running Value Jobs

This step extends serve-mode value inspection so running in-process value jobs can expose the current runtime state before job completion.

Files:
- `implementation/python/voxlogica/serve_support.py`
- `implementation/python/voxlogica/features.py`
- `implementation/python/voxlogica/execution.py`
- `implementation/python/voxlogica/main.py`
- `tests/unit/test_serve_support.py`
- `tests/unit/test_main_entrypoints.py`

What changed:
- Added `LiveRuntimeValueInspector`, attached to thread-based `value-resolve` jobs.
- `handle_run(...)` now accepts a hidden live inspector hook and attaches the compiled plan’s `materialization_store` before execution starts.
- `ExecutionEngine` now exposes `run_prepared(...)` so `handle_run(...)` can compile first, publish the live store, then execute.
- `/playground/value` now attempts live runtime inspection for `queued/running` value jobs before falling back to generic progress descriptors.
- `inspect_runtime_value_page(...)` now recognizes `InspectableSequenceValue` and emits per-item payloads with:
  - `node_id`
  - `path`
  - `status`
  - `state`
  - optional `error`
  - optional `state_reason`
  - optional `blocked_on`
- Ready child items now carry deterministic child node ids derived from the inspectable child refs.

Important scope note:
- This is **live inspection**, not yet live scheduling.
- Visible page requests can inspect and materialize inspectable items while the value job is still running because value-resolve jobs execute in-process on a thread.
- This does not yet provide explicit queue/blocked/running transitions for individual children via websocket pushes.

Validation:
- `python -m py_compile implementation/python/voxlogica/serve_support.py implementation/python/voxlogica/features.py implementation/python/voxlogica/execution.py tests/unit/test_serve_support.py tests/unit/test_main_entrypoints.py`
- `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_serve_support.py tests/unit/test_main_entrypoints.py -q -k 'live_runtime or runtime_preview or value/page or value_resolve_uses_inprocess_future or runtime_inspection'`
- `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_serve_support.py tests/unit/test_main_entrypoints.py --cov=voxlogica.serve_support --cov=voxlogica.main --cov=voxlogica.features --cov=voxlogica.execution --cov-report=term-missing -q -k 'live_runtime or runtime_preview or value/page or value_resolve_uses_inprocess_future or runtime_inspection'`

Tests added in this step:
- running value job can be inspected live via `PlaygroundJobManager.inspect_value_job_runtime(...)`
- runtime page inspection emits inspectable per-item states and deterministic child node ids
- `/api/v1/playground/value/page` uses runtime-live preview payloads while the job status is `running`

Remaining gap after step 3:
- the websocket still subscribes to a single value path, not a page window
- the UI still maps many item states through old `waiting/upstream/materialized` heuristics
- visible-page warmup is still driven by client-side polling helpers rather than page-aware server push

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

## Step 6 in progress: non-blocking child scheduling and change versions

Date: 2026-03-08

Current slice goals:

- stop `InspectableSequenceValue.page_snapshot(...)` from computing visible items inline to completion
- introduce a per-item state machine inside runtime inspectable sequences
- introduce a versioned change-notification primitive so websocket page subscriptions can wait on actual runtime changes instead of timeout loops
- make `InspectableMappedSequence` and `InspectableSubsequence` report `blocked` on upstream child refs instead of collapsing into generic pending behavior

Implementation notes for this slice:

- `InspectableSequenceValue` now carries:
  - `_item_states`
  - `_version`
  - `_change_condition`
  - `add_change_listener(...)`
  - `wait_for_change(...)`
- child work is dispatched through a small internal priority scheduler for now.
- this scheduler is intentionally local and minimal; it is a stepping stone toward the later backend-integrated scheduler abstraction.
- `ensure_item(...)` is being changed from "compute now" to "schedule and return current state" for non-inline sequence kinds.
- cheap random-access sequences (`range`, known lists) remain inline so basic runtime behavior stays deterministic.
- transformed sequences now express dependency on upstream child items explicitly:
  - mapped child `i` asks upstream child `i`
  - if upstream child `i` is not ready, the mapped item becomes `blocked` with `blocked_on=<upstream-child-ref>`
- blocked mapped/subsequence items subscribe to source-sequence changes and reschedule themselves when the upstream child becomes ready or fails.

Validation target for this slice:

- focused unit coverage in `tests/unit/test_inspectable_sequence.py`
- no regressions in `tests/unit/test_execution_trace.py`
- py_compile on the runtime modules changed in this step

Step 6 result:

- non-inline inspectable sequences now schedule child work instead of computing visible page items synchronously inside `page_snapshot(...)`
- inspectable sequences now expose:
  - `version()`
  - `wait_for_change(...)`
  - `add_change_listener(...)`
- mapped and subsequence children now surface explicit `blocked` states tied to upstream child refs
- cheap random-access roots (`range`, known list-backed sequences) remain inline for determinism

Validation completed for Step 6:

```bash
python -m py_compile implementation/python/voxlogica/inspectable_sequence.py tests/unit/test_inspectable_sequence.py tests/unit/test_execution_trace.py
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py tests/unit/test_execution_trace.py -q
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py tests/unit/test_execution_trace.py --cov=voxlogica.inspectable_sequence --cov=voxlogica.execution_strategy.strict --cov=voxlogica.execution_strategy.dask --cov-report=term-missing -q
```

Step 7 is now the transport layer:

- replace websocket timeout-loop snapshotting with `wait_for_change(...)` on the active runtime sequence when page subscriptions target an inspectable sequence
- keep HTTP snapshot endpoints unchanged
- preserve timer polling only as browser fallback

## Step 7 in progress: event-driven page streaming

Date: 2026-03-08

Current slice goals:

- make `/ws/playground/value` page subscriptions wait on actual runtime sequence changes instead of re-snapshotting every 800ms
- keep HTTP value/page endpoints unchanged
- preserve browser-side timer fallback only for no-WebSocket environments

Implementation notes for this slice:

- `inspect_runtime_value_page(...)` now includes `sequence_version` when the resolved runtime root is an `InspectableSequenceValue`.
- `RuntimeValueInspector` and `LiveRuntimeValueInspector` now expose `wait_for_change(...)`.
- `PlaygroundJobManager` now exposes `wait_for_value_job_runtime_change(...)`.
- page-mode websocket subscriptions now block on runtime sequence change notifications via `wait_for_change(...)` instead of the old fixed timeout snapshot loop.
- `_is_terminal_value_payload(...)` was tightened so generic `status="materialized"` on page payloads does not incorrectly terminate the websocket stream.

Validation target for this slice:

- py_compile on `main.py`, `serve_support.py`, `test_main_entrypoints.py`
- websocket page subscription regressions in `tests/unit/test_main_entrypoints.py`

## Follow-up fix: runtime nested overlay render URLs

Date: 2026-03-08

Observed issue after Steps 6-7:

- nested sequence pages for runtime-only values could show ready collection items, but selecting a ready overlay child triggered:
  - `Unknown store result: <child-hash>`
- root cause:
  - runtime page items used the child ref hash as `node_id` both for identity and for descriptor render URL decoration
  - overlay/image render URLs then targeted `/api/v1/results/store/<child-hash>/render/...`
  - that child hash is a live runtime child ref, not necessarily a persisted store record yet

Fix:

- keep `item.node_id` as the deterministic child ref for identity/status
- but decorate runtime item descriptors with the root sequence `node_id` plus the full child `path`, matching store-page behavior
- this keeps runtime render URLs path-based and valid before child persistence finishes

Validation:

```bash
python -m py_compile implementation/python/voxlogica/serve_support.py tests/unit/test_serve_support.py
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_serve_support.py -q -k 'inspect_runtime_value_page_reports_inspectable_item_states or uses_root_node_for_runtime_overlay_render_urls'
```

## Follow-up fix: nested `/value/page` must honor runtime-backed cached children

Date: 2026-03-08

Proven reproduction against a live local server on `127.0.0.1:8001`:

- `POST /api/v1/playground/value` for `vi_sweep_overlays` path `/0` succeeds and returns:
  - `materialization="cached"`
  - `compute_status="cached"`
  - `metadata.source="runtime"`
  - `metadata.persisted="pending"`
- `POST /api/v1/playground/value` for `/0/0` also succeeds and returns an `overlay`.
- but `POST /api/v1/playground/value/page` for `/0` fails with:
  - `Unknown store result: 7a2c2dc848ed...`

Root cause:

- `playground_value_page_endpoint()` only asked `inspect_value_job_runtime(...)` for a page when `store_status == "missing"`.
- In this failure shape, the nested child `/0` is surfaced as `materialization="cached"` from the root store hit, but the nested sequence itself is still runtime-backed:
  - `metadata.source="runtime"`
  - `metadata.persisted="pending"`
- The page endpoint therefore skipped runtime paging and rebased to:
  - `inspect_store_result_page(node_id=hash_sequence_item(root, 0), path="")`
- that child hash is not yet a persisted store record, so store paging raises `Unknown store result`.

Planned fix:

- teach `playground_value_page_endpoint()` to treat
  - `metadata.source in {"runtime", "runtime-cache", "runtime-live", "runtime-preview"}`
  - or `metadata.persisted == "pending"`
  as sufficient reason to ask the runtime inspector for a page, even when `store_status != "missing"`.
- keep the store-page path only for genuinely persisted nested containers.

Regression target:

- add an endpoint test where `/playground/value` for `/0` is `cached` and runtime-backed, and `/playground/value/page` for `/0` must return a runtime page instead of rebasing to a missing child store hash.

### Additional proven issue

After implementing the runtime-page preference, the live server still failed for `/playground/value/page` on `/0` in one important case:

- there was no matching runtime page available yet
- `/playground/value` for `/0` still returned `cached`
- `loadRecordPage()` in the UI asked page `enqueue=false` first
- `playground_value_page_endpoint()` then had no runtime page, and without a focused job it could only fail or fall back to empty/transient pages

Live proof on a fresh local debug server:

- `POST /api/v1/playground/value/page` for `/0` now returns
  - `materialization="pending"`
  - `compute_status="running"` or `queued`
  - `request_enqueued=true`
  - a transient pending page for `/0/0`, `/0/1`, ...
- `POST /api/v1/playground/value` for `/0/0` still resolves as `overlay`

Final fix for this slice:

- if `/playground/value/page` sees a runtime-backed nested sequence but no runtime page is currently available,
  it now ensures a focused `value-resolve` job itself and returns a transient pending page instead of raising
  `Unknown store result: <child-hash>`.

This is the correct behavior for the Start tab interaction path because the initial nested page request is the user's first explicit request to inspect that nested collection.

## Follow-up fix: focused child requests must promote queued inspectable items

Date: 2026-03-09

Live proof from `tests/reports/serve/voxlogica-main.log` while opening `vi_sweep_overlays`:

- explicit inner child clicks did arrive with `enqueue=True`, for example:
  - `variable=vi_sweep_overlays ... path=/1/0`
  - `path=/1/1`
  - `path=/1/2`
- later probes for those same child paths kept reporting:
  - `materialization=pending`
  - `compute_status=queued`
  - `job-completed-runtime-cache`

That proved the UI was issuing focused child requests, but the backend was not promoting already-queued child work.

Root cause, backend:

- `InspectableSequenceValue.ensure_item(...)` returned immediately for items already in `queued` or `running`.
- `_schedule_locked(...)` also returned immediately when an item was already `queued`.
- visible-page warmup could therefore queue child items at low priority, and a later focused click had no effect.

Root cause, UI:

- `loadPathRecord(...)` reused any cached path record, even if it was still a pending concrete placeholder.
- collection row clicks only changed selection; they did not force a focused re-resolve of the clicked child.

Fix:

- `implementation/python/voxlogica/inspectable_sequence.py`
  - added normalized priority helpers
  - added per-item schedule tokens and requested-priority metadata
  - a higher-priority request now supersedes a stale lower-priority queued task
  - stale queued callbacks no-op deterministically when they eventually run
  - blocked mapped/subsequence items now resume using their stored requested priority rather than defaulting back to `visible-page`
- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
  - cached path records are only reused when they are already concrete/failed or when the caller is not asking for enqueue fallback
- `implementation/ui/src/lib/components/tabs/StartValueCanvas.svelte`
  - clicking an active collection item (`not_loaded/queued/blocked/running/persisting`) now forces an immediate focused `loadPathRecord(...)`

Validation:

```bash
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py -q
PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_inspectable_sequence.py --cov=voxlogica.inspectable_sequence --cov-report=term-missing -q
npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js
npm --prefix implementation/ui run build
```

## Compute Log Activity + Graph/Dream Tabs (2026-03-10)

Changes in progress:

- Added compute activity store and UI feed so every `/api/v1/playground/value` and `/api/v1/playground/value/page` request is logged in the Compute Log tab.
  - `implementation/ui/src/lib/stores/computeActivity.js`
  - `implementation/ui/src/lib/components/tabs/ComputeLogTab.svelte`
  - `implementation/ui/src/lib/api/client.js`
- Moved oneiric visualization into a dedicated tab driven by a shared store:
  - `implementation/ui/src/lib/stores/dreamStore.js`
  - `implementation/ui/src/lib/components/shared/DreamVisual.svelte`
  - `implementation/ui/src/lib/components/tabs/DreamTab.svelte`
  - `implementation/ui/src/App.svelte`
- Added a new Compute Graph tab to visualize symbolic nodes, dependencies, operator/kind, and variable names:
  - `implementation/ui/src/lib/components/tabs/GraphTab.svelte`
  - `implementation/ui/src/App.svelte`
  - `implementation/ui/src/app.css`
- Backend endpoint for symbolic graph (pending test pass):
  - `implementation/python/voxlogica/main.py` `/api/v1/playground/graph`
  - `implementation/ui/src/lib/api/client.js` `getPlaygroundGraph`

Tests added:
- `tests/unit/test_main_entrypoints.py` now exercises `/api/v1/playground/graph`.
- `implementation/ui/src/lib/api/client.test.js` verifies compute activity logging on value/page requests.

Pending verification:
- Run UI tests:
  - `npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js`
  - `npm --prefix implementation/ui run test -- src/lib/api/client.test.js`
- Run python unit tests:
  - `PYTHONPATH=implementation/python .venv/bin/python -m pytest tests/unit/test_main_entrypoints.py -q`
- Rebuild UI bundle for serve mode:
  - `npm --prefix implementation/ui run build`

Notes:
- Start tab now pushes dream state into `dreamStore` instead of rendering the dream overlay directly; the Oneiric Trace tab renders it.
- Compute activity logging is centralized in `apiRequest` to ensure ALL value/page traffic is recorded without per-caller duplication.

## Follow-up fix: Start tab must not let stale pending page snapshots clobber already-resolved children

Date: 2026-03-11

Proven issue:

- in the Start tab, some collection rows could still render as `not_loaded` / `status=pending`
  even though clicking the row immediately revealed the real concrete value in the stage
- this was reproducible for cached sequence items such as `flair_images[5]`
- backend inspection on a fresh DB proved the child value itself was already concrete/cached,
  so the remaining fault was in frontend page-state reconciliation

Root cause:

- `StartTab.svelte` keeps two related caches:
  - path records for concrete child resolves (`/5`, `/6`, ...)
  - page snapshots for collection views (`/`)
- a later pending page snapshot from `/playground/value/page` could overwrite a row that had
  already been concretely resolved through `/playground/value`
- the page merge path was not reusing the stronger child record when rebuilding page rows

Secondary regression caught while fixing it:

- `applyRecordPagePayload(...)` called `cacheRecordPage(..., variableName)` without defining
  `variableName` in scope
- this caused a Start tab runtime failure (`variableName is not defined`) and collection panes
  could disappear entirely

Fix:

- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
  - added `mergePathRecordIntoPageItem(...)`
  - page rows are now merged with any already-cached concrete child record before caching
  - `cacheRecordPage(...)` and `applyRecordPagePayload(...)` now carry the explicit source variable
    through the page caching path, so row/path reconciliation uses the correct variable namespace
- `implementation/ui/src/lib/components/tabs/StartTab.test.js`
  - added a regression test ensuring a later pending page snapshot does not overwrite a concrete
    child already cached from `/playground/value`

Validation:

```bash
npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js
```

## Fresh API verification: nested `vi_sweep_overlays` is healthy on the backend

Date: 2026-03-11

Rechecked on a fresh debug server and clean HOME-backed results DB:

- submitted the exact `vi_sweep_overlays` workflow through `/api/v1/playground/jobs`
- resolved:
  - `/api/v1/playground/value` for `/`, `/0`, `/0/0`, `/1`, `/1/0`
  - `/api/v1/playground/value/page` for `/`, `/0`, `/1`

Proven backend behavior:

- root value `/` is `sequence`, `materialization="cached"`, `compute_status="cached"`
- nested `/0` and `/1` are `sequence`
- leaf `/0/0` and `/1/0` are `overlay`
- nested page items under `/0` and `/1` are already `state="ready"` with `descriptor.vox_type="overlay"`
- overlay descriptors emit `render.kind="medical-overlay"`
- layer render URLs point to the root node store path with descendant child paths, e.g.
  - `/api/v1/results/store/<root>/render/nii?path=/0/0/0`
  - `/api/v1/results/store/<root>/render/nii?path=/0/0/1`
- direct GETs on those emitted render URLs return `200`

Conclusion:

- if the UI still shows `Not Found` for these overlay leaves after this point, the fault is no longer in
  backend materialization or overlay URL generation on a fresh DB
- remaining candidates are frontend state/view reconciliation, stale browser state, or viewer-specific rendering behavior

## Follow-up fix: nested collection pages must not look terminal while still loading

Date: 2026-03-11

Proven issue:

- the live API for the exact `vi_sweep_overlays` workflow returned:
  - `/` -> cached sequence with outer items
  - `/0` -> cached nested page with overlay items
  - `/0/0` -> cached overlay leaf with `render.kind="medical-overlay"`
- despite that, the Start tab could still show `No values yet` for a selected nested collection
  while the page was still pending/polling, which made the UI look stuck even though the backend
  was still driving child pages forward

Root cause:

- `StartValueCanvas.svelte` treated the state `!items.length && !loading && !error` as terminal empty
- it did not distinguish:
  - truly empty collection
  - pending empty page still subscribed/polling

Fix:

- `implementation/ui/src/lib/components/tabs/StartValueCanvas.svelte`
  - added `pendingCollectionStateFor(...)`
  - when a collection page is empty but `pagePollingForRecord(...)` is active, or the selected
    collection record is still pending, the nested stage now stays in loading state
  - the left-hand collection index also keeps its loading skeleton while the empty pending page
    is still active instead of implying completion

Frontend regression tests:

- `implementation/ui/src/lib/components/tabs/StartTab.test.js`
  - `keeps nested pending collection pages in a loading state instead of showing an empty terminal message`
  - `renders cached nested overlays directly from page snapshots without waiting on a child resolve`
  - updated the older pending-child click regression so it reflects the intended no-churn behavior:
    one explicit resolve for a clicked pending child, then direct nested page loading from page snapshots

Validation:

```bash
npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js
npm --prefix implementation/ui run build
```

## Update: operations terminology, inline help, and failure contrast

Date: 2026-03-12

Scope:

- clarify the in-app operations log wording so it distinguishes transport, live subscriptions,
  and pending backend work
- add inline `(i)` explanations in both the Start tab operations panel and the Compute Log tab
- fix unreadable failure styling (`red` text over `pink` surfaces)

Implemented:

- `implementation/ui/src/lib/constants/computeActivityHelp.js`
  - shared help rows used by both Start and Compute Log panels
- `implementation/ui/src/lib/components/tabs/StartTab.svelte`
  - renamed ambiguous summaries:
    - `Watching ...` -> `Live updates active ...`
    - `Loading ...` -> `Fetching nested value ...`
    - `Updating ...` -> `Waiting for nested value ...`
    - `Loading page ...` -> `Fetching page ...`
    - `Updating page ...` -> `Waiting for page items ...`
    - `Request sent ...` / `Response received ...` -> explicit resolve request/reply wording
  - added an inline info button and help card
- `implementation/ui/src/lib/components/tabs/ComputeLogTab.svelte`
  - added the same inline info button and help card for consistency
- `implementation/ui/src/app.css`
  - restyled failed collection items, failed status pills, and viewer error cards to use dark text
    on soft tinted backgrounds instead of low-contrast red-on-pink
- `implementation/ui/src/lib/components/tabs/StartTab.test.js`
  - added a regression/behavior test for the inline explanations panel

Persistence note:

- the operations log is still session-local browser state only
- it is not persisted across page reloads or browser restarts
- the new help card says this explicitly

Validation:

```bash
npm --prefix implementation/ui run test -- src/lib/stores/computeActivity.test.js
npm --prefix implementation/ui run test -- src/lib/components/tabs/StartTab.test.js
npm --prefix implementation/ui run test -- src/lib/api/client.test.js
npm --prefix implementation/ui run build
```
