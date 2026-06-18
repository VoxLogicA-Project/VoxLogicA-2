# nnUNet Namespace Documentation

## Overview

The `nnunet` namespace integrates VoxLogicA-2 with nnU-Net v2 for medical image segmentation. Training uses **case sequences**; each case carries its modalities and label in one structure. Training returns an opaque **model handle**. Load a **predictor** from that handle once, then call `predict` per image to obtain label images.

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
nnunet.train(training_cases, work_root, modalities, configuration, nfolds, dataset_name, device, trainer)
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
| 7 | `trainer` | no | nnU-Net trainer class. Default: `"nnUNetTrainer"` |

Use `"nnUNetTrainer_10epochs"` for short CPU demos and tests.

**Training case shape:**

```voxlogica
[case_id, [modality_volume, ...], label_volume]
```

- `case_id`: string identifier (sanitized for nnU-Net filenames)
- `modality_volume`: numpy array or SimpleITK 2D image per channel
- `label_volume`: segmentation mask (0 = background, 1 = foreground)

**Returns:** model handle (`vox_kind = "nnunet_model"`). Pass it to `nnunet.make_predictor`.

**Resume:** Re-running `train` with the same `work_root` skips folds that already have `checkpoint_final.pth`.

### nnunet.make_predictor

Loads an nnU-Net predictor from a trained model handle. Call once, then reuse with `predict` (for example inside `map`).

**Signature:**

```voxlogica
nnunet.make_predictor(model, [device], [folds])
```

| # | Name | Required | Description |
|---|------|----------|-------------|
| 0 | `model` | yes | Handle returned by `nnunet.train` |
| 1 | `device` | no | `"cpu"` or `"cuda"`. Default: device stored on the model handle |
| 2 | `folds` | no | Folds to use. Default: folds trained in the model handle |

**Returns:** predictor handle (`vox_kind = "nnunet_predictor"`).

### nnunet.predict

Segments one case and returns a label image (SimpleITK).

**Signature:**

```voxlogica
nnunet.predict(predictor, image)
```

| # | Name | Required | Description |
|---|------|----------|-------------|
| 0 | `predictor` | yes | Handle returned by `nnunet.make_predictor` |
| 1 | `image` | yes | One modality volume, or a list of volumes for multi-modal models |

**Returns:** segmentation label image.

## Complete workflow

See also the gallery example: `doc/gallery/programs/nnunet/nnunet-circle-segmentation.imgql`.

```voxlogica
import "geom"
import "strings"
import "nnunet"

work_root = "/tmp/nnunet_work"

let train_case(i) = [
  concat("case_", format_string("{:.0f}", i)),
  [geom.circle(geom.blank(64, 64, 0), 20, 32, 10, 200)],
  geom.circle(geom.blank(64, 64, 0), 20, 32, 10, 1)
]

training = [train_case(0), train_case(1), train_case(2)]

model = nnunet.train(training, work_root, ["intensity"], "2d", 1, "MyDataset", "cpu")

predictor = nnunet.make_predictor(model)

test_image = geom.circle(geom.blank(64, 64, 0), 40, 32, 12, 200)
segmentation = nnunet.predict(predictor, test_image)
```

Use at least five training cases when relying on nnU-Net’s default cross-validation splits, or set `nfolds` to `1` for small synthetic demos.

## Work directory layout

```
work_root/
  nnUNet_raw/              # materialized training images and labels
  nnUNet_preprocessed/     # nnU-Net preprocessing output
  nnUNet_results/          # trained model checkpoints
  voxlogica_manifest.json  # dataset id and training state
```

Dataset IDs are allocated automatically (from 900 upward) and reused for the same `work_root`.

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| `nnunetv2 not installed` | `pip install nnunetv2` |
| `torch` missing | Install from https://pytorch.org/ |
| `predict requires a predictor handle` | Call `make_predictor` first |
| `n_splits` / sample count errors | Add more training cases or lower `nfolds` |
| CUDA errors on CPU-only hosts | Pass `"cpu"` as the device argument to `train` |

Debug logging:

```bash
./voxlogica run --debug your_program.imgql
```

## Implementation notes

Training materializes case sequences into nnU-Net’s on-disk layout and runs `nnUNetv2_plan_and_preprocess` and `nnUNetv2_train`. Inference uses the nnU-Net `nnUNetPredictor` Python API so the model loads once per predictor handle. Volumes are 2D numpy or SimpleITK images. Implementation lives under `implementation/python/voxlogica/primitives/nnunet/`.
