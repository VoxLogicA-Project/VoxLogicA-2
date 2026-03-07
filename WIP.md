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
- `npm --prefix implementation/ui run test`
  - Passed.
- `npm --prefix implementation/ui run build`
  - Passed.
