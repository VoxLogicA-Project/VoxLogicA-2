# nnUNet Namespace Documentation

## Overview

The `nnunet` namespace integrates VoxLogicA-2 with nnU-Net v2 for medical image segmentation. Training and prediction use **case sequences**: each case carries its modalities and (for training) label in one structure. Training returns an opaque **model handle** that you pass directly to `predict`.

## Prerequisites

1. **nnunetv2** installed (`pip install nnunetv2`)
2. **PyTorch** installed for your hardware (CPU/GPU)
3. Python >= 3.9

Check the runtime with `nnunet.env_check()`.

## Functions

### nnunet.env_check

Returns a mapping with `ready`, `torch_available`, `nnunetv2_available`, and `issues`.

```voxlogica
import "nnunet"
status = nnunet.env_check()
```

### nnunet.train

Trains an nnU-Net model from a sequence of training cases.

**Signature:**

```voxlogica
nnunet.train(training_cases, work_root, modalities, configuration, nfolds, dataset_name, device)
```

**Arguments:**

| # | Name | Required | Description |
|---|------|----------|-------------|
| 0 | `training_cases` | yes | Sequence of training cases (see below) |
| 1 | `work_root` | yes | Directory for nnU-Net raw/preprocessed/results data |
| 2 | `modalities` | no | Modality names, e.g. `["T1"]`. Omitted → auto `ch0`, `ch1`, … |
| 3 | `configuration` | no | nnU-Net config (`"2d"`, `"3d_fullres"`, …). Default: `"2d"` |
| 4 | `nfolds` | no | Folds to train. Default: `5` |
| 5 | `dataset_name` | no | Human-readable dataset name. Default: `"VoxLogicA"` |
| 6 | `device` | no | `"cpu"` or `"cuda"`. Default: `"cpu"` |

**Training case shape:**

```voxlogica
[case_id, [modality_volume, ...], label_volume]
```

- `case_id`: string identifier (sanitized for nnU-Net filenames)
- `modality_volume`: numpy array or SimpleITK 2D image per channel
- `label_volume`: segmentation mask (0 = background, 1 = foreground)

**Returns:** model handle (`vox_kind = "nnunet_model"`) including:

- `status`, `work_root`, `dataset_id`, `dataset_folder`
- `configuration`, `modalities`, `trained_folds`, `trainer_dir`
- `labels`, `file_ending`

Pass this handle to `nnunet.predict`. Do not parse `trainer_dir` or dataset IDs yourself.

**Resume:** Re-running `train` with the same `work_root` skips folds that already have `checkpoint_final.pth`.

### nnunet.predict

Runs inference from a model handle and a sequence of prediction cases.

**Signature:**

```voxlogica
nnunet.predict(model, prediction_cases, [output_subdir], [folds], [save_probabilities])
```

**Arguments:**

| # | Name | Required | Description |
|---|------|----------|-------------|
| 0 | `model` | yes | Handle returned by `nnunet.train` |
| 1 | `prediction_cases` | yes | Sequence of prediction cases |
| 2 | `output_subdir` | no | Subfolder under `work_root/materialized/`. Default: `"predictions"` |
| 3 | `folds` | no | Folds to use. Default: folds trained in the model handle |
| 4 | `save_probabilities` | no | Export probability maps. Default: `false` |

**Prediction case shape:**

```voxlogica
[case_id, [modality_volume, ...]]
```

**Returns:**

- `status`: `"success"`
- `cases`: list of `{case_id, segmentation_path}`
- `prediction_files`, `output_path`, `num_predictions`
- `model`: echo of the input handle

## Complete workflow

See also the gallery example: `doc/gallery/programs/nnunet/nnunet-circle-segmentation.imgql`.

```voxlogica
import "geom"
import "strings"
import "nnunet"

work_root = "/tmp/nnunet_work"

let train_case(i) = [
  concat("case_", format_string("{}", i)),
  [geom.circle(geom.blank(64, 64, 0), 20, 32, 10, 200)],
  geom.circle(geom.blank(64, 64, 0), 20, 32, 10, 1)
]

training = [train_case(0), train_case(1), train_case(2)]

model = nnunet.train(training, work_root, ["intensity"], "2d", 1, "MyDataset", "cpu")

let test_case = [
  "held_out",
  [geom.circle(geom.blank(64, 64, 0), 40, 32, 12, 200)]
]

predictions = nnunet.predict(model, [test_case])
```

Use at least five training cases when relying on nnU-Net’s default cross-validation splits, or set `nfolds` to `1` for small synthetic demos.

## Work directory layout

```
work_root/
  nnUNet_raw/              # materialized training images and labels
  nnUNet_preprocessed/     # nnU-Net preprocessing output
  nnUNet_results/          # trained model checkpoints
  materialized/            # per-run inference inputs and predictions
  voxlogica_manifest.json  # dataset id and training state
```

Dataset IDs are allocated automatically (from 900 upward) and reused for the same `work_root`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `VOXLOGICA_NNUNET_TRAINER` | Optional nnU-Net trainer class (e.g. `nnUNetTrainer_10epochs` for short test runs) |

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| `nnunetv2 not installed` | `pip install nnunetv2` |
| `torch` missing | Install from https://pytorch.org/ |
| `predict requires a model handle` | Pass the dict from `train`, not a path |
| `n_splits` / sample count errors | Add more training cases or lower `nfolds` |
| CUDA errors on CPU-only hosts | Pass `"cpu"` as the device argument to `train` |

Debug logging:

```bash
./voxlogica run --debug your_program.imgql
```

## Implementation notes

The namespace materializes case sequences into nnU-Net’s on-disk layout, runs `nnUNetv2_plan_and_preprocess` and `nnUNetv2_train`, then `nnUNetv2_predict`. Volumes are written as 2D NIfTI (numpy or SimpleITK inputs). Implementation lives under `implementation/python/voxlogica/primitives/nnunet/`.
