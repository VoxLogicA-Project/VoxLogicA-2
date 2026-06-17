# nnUNet Primitives — Design

**Status:** draft (implementation target)  
**Scope:** `implementation/python/voxlogica/primitives/nnunet/`  
**Last updated:** 2026-06-17

## 1. Problem statement

VoxLogicA needs first-class nnUNet v2 training and prediction that:

1. Accepts **VoxLogicA sequences** (not only Dask bags or pre-built directory trees).
2. **Pairs each case’s modalities with its ground-truth label** without forcing users to reason about nnUNet’s on-disk layout.
3. **Hides nnUNet’s numeric identifiers** (dataset IDs, 4-digit channel suffixes, fold numbers, trainer/plan folder names) behind stable VoxLogicA handles.
4. Returns **typed, inspectable results** (model handle + prediction sequence), not opaque path strings that must be regex-parsed.

The current `kernels.py` implementation is a useful prototype but leaks nnUNet internals:

- `train` requires Dask `bag.compute()` inputs and flat `(case_id, modality, array)` tuples in a separate labels bag.
- `predict` accepts a `model_path` string and parses `Dataset(\d+)_` from the folder name.
- Users must supply `dataset_id` (3-digit integer) and know nnUNet env vars / directory roots.
- Gallery examples use `result["model_path"]` but string-key mapping access is not reliably available in ImgQL yet.

## 2. Design goals

| Goal | Rationale |
|------|-----------|
| **Stable logical IDs** | User-facing case names (`"patient_42"`) never become nnUNet channel suffixes or dataset integers in the API. |
| **Single dataset sequence** | One training sequence encodes case + modalities + label; pairing is structural, not join-by-key across two bags. |
| **Opaque model handle** | After training, callers hold a `NnUNetModel` value (mapping-backed), not a raw `nnUNet_results/...` path. |
| **Reproducible materialization** | On-disk nnUNet layout is an implementation detail under a managed work root. |
| **Resume-safe** | Re-running with the same work root + model handle continues folds/checkpoints (nnUNet native behaviour). |
| **Inspectable** | Handles and results use `vox_type=mapping` / `sequence` with documented keys until a record type exists. |

Non-goals for v1 of this redesign:

- Replacing nnUNet preprocessing/training CLIs.
- Supporting nnUNet v1 `Task*` layouts.
- Region-based training or custom trainer classes (can be added later via options).

## 3. nnUNet v2 technical reference (external contract)

Sources: [dataset_format.md](https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/dataset_format.md), [how_to_use_nnunet.md](https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/how_to_use_nnunet.md), [dataset_format_inference.md](https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/dataset_format_inference.md).

### 3.1 Environment roots

nnUNet v2 expects three roots (env vars or install-time config):

| Variable | Purpose |
|----------|---------|
| `nnUNet_raw` | Source datasets (`DatasetXXX_Name/`) |
| `nnUNet_preprocessed` | Fingerprints, plans, preprocessed caches |
| `nnUNet_results` | Trained model checkpoints and validation outputs |

VoxLogicA will set these under a single **work root** chosen by the user (or default temp dir). Users never pass `nnUNet_raw` paths in the public API.

### 3.2 Raw dataset layout (what we materialize)

```text
nnUNet_raw/Dataset{ID:03d}_{Name}/
├── dataset.json
├── imagesTr/
│   ├── {CASE}_{0000}.nii.gz
│   ├── {CASE}_{0001}.nii.gz
│   └── ...
├── labelsTr/
│   ├── {CASE}.nii.gz
│   └── ...
└── imagesTs/          # optional, not used by training CLI
```

**Case identifier** (`CASE`): unique string per training case; links images and label.

**Channel suffix** (`0000`, `0001`, …): 4-digit modality index — **not** a semantic name on disk. Semantic names live only in `dataset.json → channel_names`.

**Label file**: `{CASE}.{file_ending}` — no channel suffix.

Constraints from nnUNet:

- All cases must expose the **same channels in the same order**.
- Image/label geometry must match per case.
- Label integers must be consecutive starting at `0` (background). Non-binary labels may be sanitized to `{0,1}` when needed (current behaviour).
- `file_ending` must match between images and labels (default `.nii.gz`).

### 3.3 `dataset.json` (minimal)

```json
{
  "channel_names": { "0": "T1", "1": "T2" },
  "labels": { "background": 0, "foreground": 1 },
  "numTraining": 32,
  "file_ending": ".nii.gz"
}
```

`channel_names` keys are strings `"0"`, `"1"`, …; values are human modality names (affect normalization: `"CT"` vs others).

### 3.4 Training pipeline (CLI)

```bash
nnUNetv2_plan_and_preprocess -d DATASET_ID --verify_dataset_integrity -c CONFIGURATION
nnUNetv2_train DATASET_ID CONFIGURATION FOLD
```

- `DATASET_ID`: integer **or** folder name `DatasetXXX_Name`.
- `CONFIGURATION`: `2d`, `3d_fullres`, `3d_lowres`, `3d_cascade_fullres` (not all exist for every dataset).
- `FOLD`: `0`–`4` for default 5-fold CV, or `all` for single model on all data.
- Checkpoints saved every 50 epochs; `--c` continues training.

**Results layout** (auto-generated, must not be user-facing):

```text
nnUNet_results/DatasetXXX_Name/
└── nnUNetTrainer__nnUNetPlans__3d_fullres/
    ├── fold_0/ … fold_4/
    ├── dataset.json
    ├── plans.json
    └── dataset_fingerprint.json
```

Each `fold_k/` contains `checkpoint_final.pth`, `debug.json`, `progress.png`, `validation/`, etc.

### 3.5 Inference (CLI)

Input folder must mirror `imagesTr` naming: `{CASE}_{XXXX}.nii.gz`.

```bash
nnUNetv2_predict -i INPUT -o OUTPUT -d DATASET_ID -c CONFIGURATION [-f FOLD ...] [--save_probabilities]
```

Output segmentations: `{CASE}.nii.gz` (channel suffix omitted).

Default: ensemble of all 5 folds. Use `-f all` when training the `all` fold only.

### 3.6 Numeric identifiers we must absorb

| nnUNet concept | Example | VoxLogicA surface |
|----------------|---------|-----------------|
| Dataset ID | `5` → `Dataset005_Prostate` | Internal; assigned once per work root / model handle |
| Channel index | `_0000`, `_0001` | Derived from modality list order |
| Fold index | `0`–`4` | Training option `nfolds`; stored in model handle |
| Trainer folder | `nnUNetTrainer__nnUNetPlans__3d_fullres` | Stored in model handle, not constructed by users |
| Results path | `.../fold_2/checkpoint_final.pth` | Never returned as primary API |

## 4. VoxLogicA data model

### 4.1 No `record` type (yet)

VoxLogicA has `sequence` and `mapping` (`vox_type`), not a first-class `record`. Until records land, use:

- **Case** = length-3 sequence: `[case_id, modalities, label]`
- **Modalities** = sequence of volumes aligned with the `modalities` name list in train options
- **Alternates** (supported by adapter, not primary docs): mapping `{"id", "modalities", "label"}` when mapping field access works

### 4.2 Case pairing contract

```text
training_cases: sequence of case
case          := [case_id, modality_volumes, label_volume]

case_id           : string | int          # logical identifier, stable across train/predict
modality_volumes  : sequence of image    # len == len(modalities_names); same order
label_volume      : image | ndarray      # integer segmentation, background = 0
```

**Pairing rule:** index `i` in the outer training sequence owns all three components; no cross-bag join.

**Validation (materialize time):**

- `len(modality_volumes) == len(modalities_names)`
- `case_id` non-empty after sanitization
- duplicate `case_id` → error
- missing/empty label → error for training
- geometry mismatch (shape/spacing) → error with case context

### 4.3 Prediction cases

```text
prediction_cases: sequence of [case_id, modality_volumes]
```

No label. Same modality count/order as training.

### 4.4 Model handle (`NnUNetModel`)

Opaque mapping returned by `train`. Required keys:

| Key | Type | Meaning |
|-----|------|---------|
| `vox_kind` | string | `"nnunet_model"` (convention for UI/adapters) |
| `work_root` | string | Managed directory containing nnUNet env roots |
| `dataset_fingerprint` | string | Stable slug, e.g. `vla-{hash}` — **not** user-chosen `Dataset999` |
| `dataset_id` | int | Internal nnUNet integer assigned at first materialization |
| `dataset_name` | string | Human name segment (`VoxLogicA_MyRun`) |
| `configuration` | string | e.g. `3d_fullres` |
| `trainer_dir` | string | Absolute path to `nnUNetTrainer__nnUNetPlans__*` |
| `modalities` | sequence of string | Channel names in order |
| `file_ending` | string | Default `.nii.gz` |
| `nfolds` | int | Folds trained or planned |
| `trained_folds` | sequence of int | Completed fold indices |
| `labels` | mapping | Label name → int (from dataset.json) |
| `materialization_manifest` | string | Path to JSON manifest (case_id ↔ on-disk filenames) |

Callers pass the **whole handle** to `predict`; they do not pass `dataset_id` or results paths.

### 4.5 Prediction result

```text
prediction_result: mapping {
  status: "success" | "failed",
  model: <NnUNetModel handle>,
  cases: sequence of mapping {
    case_id: string,
    segmentation: image | path,   # prefer image when size allows
    segmentation_path: string     # always present under work root
  }
}
```

## 5. Public primitive API

### 5.1 `nnunet.train`

```text
nnunet.train(training_cases, options?)
```

**Arguments**

- `training_cases` (required): sequence of cases (§4.2).
- `options` (optional mapping or defaulted kwargs):

| Option | Default | Notes |
|--------|---------|-------|
| `work_root` | temp / attr | Single directory; owns `nnUNet_*` subdirs |
| `dataset_name` | `"VoxLogicADataset"` | Human-readable; combined with internal ID |
| `modalities` | inferred or required | List of names; if omitted, infer from first case length with generic names `ch0`, `ch1`, … |
| `configuration` | `"3d_fullres"` | nnUNet config string |
| `nfolds` | `5` | Number of CV folds to run |
| `device` | `"gpu"` | `gpu` / `cpu`; maps to `CUDA_VISIBLE_DEVICES` |
| `labels` | `{background:0, foreground:1}` | Written to dataset.json |
| `file_ending` | `".nii.gz"` | |
| `verify_integrity` | `true` | Pass `--verify_dataset_integrity` on first plan |
| `continue_training` | `true` | Skip completed folds when resuming |

**Returns:** `NnUNetModel` handle (§4.4).

**Behaviour**

1. Validate sequence structure.
2. Assign or reuse `dataset_id` via manifest in `work_root` (see §6).
3. Materialize nnUNet raw layout + `dataset.json`.
4. Run `plan_and_preprocess` if not done for this handle.
5. Train folds `0 .. nfolds-1`, skipping folds present in `trained_folds` when resuming.
6. Populate `trainer_dir` and return handle.

### 5.2 `nnunet.predict`

```text
nnunet.predict(model, prediction_cases, options?)
```

**Arguments**

- `model` (required): `NnUNetModel` handle from `train`.
- `prediction_cases` (required): sequence (§4.3).
- `options` (optional):

| Option | Default | Notes |
|--------|---------|-------|
| `folds` | all trained folds | Passed to `-f` |
| `save_probabilities` | `false` | |
| `output_subdir` | `"predictions"` | Under `work_root` |

**Returns:** prediction result mapping (§4.5).

**Behaviour**

1. Materialize inference input folder from cases using manifest channel order.
2. Run `nnUNetv2_predict` with `-d` and `-c` from handle (not user-supplied).
3. Load or reference output segmentations; build per-case result sequence.

### 5.3 Retained / deprecated entry points

| Primitive | Fate |
|-----------|------|
| `train` (bag-based) | Refactor to call shared materializer; deprecate bag requirement |
| `train_directory` | Keep for users with existing nnUNet folders; returns same handle shape |
| `predict` (path-based) | Accept handle **or** legacy path during transition |
| `env_check` | Unchanged |

## 6. Hiding numeric nnUNet identifiers

### 6.1 Work root layout (VoxLogicA managed)

```text
{work_root}/
├── voxlogica_manifest.json      # authoritative mapping (see below)
├── nnUNet_raw/
├── nnUNet_preprocessed/
├── nnUNet_results/
├── materialized/
│   ├── raw/                     # copy of or symlink to nnUNet_raw/Dataset*
│   ├── inference/{run_id}/
│   └── predictions/{run_id}/
└── logs/
```

### 6.2 `voxlogica_manifest.json`

Single source of truth to avoid re-deriving IDs from paths:

```json
{
  "schema_version": 1,
  "dataset_id": 42,
  "dataset_folder": "Dataset042_VoxLogicA_MyRun",
  "dataset_name": "VoxLogicA_MyRun",
  "modalities": ["T1", "T2"],
  "configuration": "3d_fullres",
  "trainer_dir": "nnUNet_results/Dataset042_.../nnUNetTrainer__nnUNetPlans__3d_fullres",
  "cases": {
    "patient_42": {
      "sanitized_id": "patient_42",
      "channels": ["patient_42_0000.nii.gz", "patient_42_0001.nii.gz"],
      "label": "patient_42.nii.gz"
    }
  },
  "trained_folds": [0, 1]
}
```

**Dataset ID allocation:** scan `work_root/nnUNet_raw/Dataset*_*` or persist `dataset_id` in manifest on first run. Never require the user to pick a 3-digit ID.

**Case ID sanitization:** `[^A-Za-z0-9_-]` → `_` (existing `_sanitize_case_name`). Manifest stores both logical and sanitized IDs.

**Channel indices:** always `enumerate(modalities)` → `{i:04d}`; user modality **names** only appear in `dataset.json` and the handle.

### 6.3 Why not expose paths?

`predict` today does:

```python
match = re.search(r"Dataset(\d{1,3})_", model_path.name)
```

That breaks when:

- folder names differ in zero-padding (`Dataset5` vs `Dataset005`),
- symlinks are used (current code creates unpadded symlink),
- trainer directory depth changes between nnUNet versions.

The handle + manifest removes regex parsing from the hot path.

## 7. Module structure (implementation plan)

```text
primitives/nnunet/
├── DESIGN.md           # this file
├── README.md           # user-facing docs (update after impl)
├── __init__.py         # exports
├── kernels.py          # thin primitive entrypoints (train/predict/…)
├── materialize.py      # sequence → nnUNet raw + inference folders
├── manifest.py         # read/write voxlogica_manifest.json, ID allocation
├── pipeline.py         # subprocess wrappers (plan, train, predict)
├── types.py            # handle builders, validation helpers
└── legacy.py           # optional: bag/path adapters calling materialize
```

Shared internal flow:

```text
train:
  validate_cases → manifest.allocate → materialize_raw → write_dataset_json
  → plan_and_preprocess → train_folds → build_model_handle

predict:
  validate_cases → materialize_inference → nnUNetv2_predict
  → collect_outputs → build_prediction_result
```

## 8. ImgQL examples (target)

```imgql
import "nnunet"

// Each case: [id, [modality volumes...], label]
training = [
  ["case001", [t1_001, t2_001], seg_001],
  ["case002", [t1_002, t2_002], seg_002]
]

model = nnunet.train(training, {
  "work_root": "/data/runs/exp1",
  "modalities": ["T1", "T2"],
  "configuration": "3d_fullres",
  "nfolds": 5
})

test_cases = [
  ["case101", [t1_101, t2_101]],
  ["case102", [t1_102, t2_102]]
]

predictions = nnunet.predict(model, test_cases)
```

Until mapping literals and string-key access exist in ImgQL, options may remain positional kwargs on the primitive (current style).

## 9. Testing requirements

Add under `tests/`:

| Test | Type | Notes |
|------|------|-------|
| Case validation | unit | bad lengths, duplicate ids, empty id |
| Sanitization + manifest round-trip | unit | logical ↔ on-disk names |
| Materialize sequence → raw layout | unit | mock images (small ndarray), assert filenames |
| Dataset ID allocation | unit | fresh work_root vs resume |
| Handle builder | unit | no raw regex on paths |
| Train/predict integration | optional/integration | skip if `nnunetv2` not installed; mark with env guard |

Existing nnUNet tests (if any) should be updated to expect handle-shaped returns.

## 10. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Large volumes inlined in sequences | Document; future: lazy image refs / paths in inner sequence |
| nnUNet version drift in results folder names | Store `trainer_dir` in manifest after first successful train |
| Dataset ID collisions in shared work_root | Manifest is authoritative; conflict detection on `dataset_name` |
| Binary-only assumption in `dataset.json` | Accept `labels` option; inspect unique label values per case |
| ImgQL lacks mapping/options syntax | Support positional optional args alongside mapping (Python kernel) |

## 11. Implementation phases

1. **Phase A — foundation:** `manifest.py`, `materialize.py`, `types.py`; unit tests.
2. **Phase B — `train` sequence path:** new code path in `train`; return handle; keep legacy bag adapter.
3. **Phase C — `predict` handle path:** accept handle; return case sequence; legacy path fallback.
4. **Phase D — docs/gallery:** update README, gallery programs, `doc/dev` cross-link if needed.

## 12. References

- nnUNet v2 dataset format: https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/dataset_format.md
- nnUNet v2 usage / training / results layout: https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/how_to_use_nnunet.md
- nnUNet v2 inference input format: https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/dataset_format_inference.md
- VoxLogicA store types: `doc/spec/store-format-voxpod-v1.md`
- VoxLogicA primitives API: `doc/dev/primitives-api.md`
