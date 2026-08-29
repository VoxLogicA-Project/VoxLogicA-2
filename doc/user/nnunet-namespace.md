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
nnunet.train(training_cases, work_root, modalities, configuration, nfolds, dataset_name, device, trainer, plans, postprocess, pretrained)
```

**Arguments:**

| # | Name | Required | Description |
|---|------|----------|-------------|
| 0 | `training_cases` | yes | Sequence of training cases (see below) |
| 1 | `work_root` | yes | Directory for nnU-Net raw/preprocessed/results data |
| 2 | `modalities` | no | Modality names, e.g. `["T1"]`. Omitted → auto `ch0`, `ch1`, … |
| 3 | `configuration` | no | One config (`"3d_fullres"`) or several (`["2d", "3d_fullres", "3d_lowres"]`). Default: `"2d"` |
| 4 | `nfolds` | no | Folds to train. Default: `5` |
| 5 | `dataset_name` | no | Human-readable dataset name. Default: `"VoxLogicA"` |
| 6 | `device` | no | `"cpu"` or `"cuda"`. Default: `"cpu"` |
| 7 | `trainer` | no | nnU-Net trainer class. Default: `"nnUNetTrainer"` |
| 8 | `plans` | no | Plans identifier, i.e. the architecture preset. Default: `"nnUNetPlans"`; the residual-encoder presets are `"nnUNetResEncUNetMPlans"`, `"…LPlans"`, `"…XLPlans"` |
| 9 | `postprocess` | no | Run nnU-Net's own postprocessing step. Default: `"true"` |
| 10 | `pretrained` | no | Path to a checkpoint to start from instead of random init. Default: none |

Use `"nnUNetTrainer_10epochs"` for short CPU demos and tests.

**Defaults follow nnU-Net's own recommendations.** `nfolds` is 5 because the
documented workflow trains five folds and predicts with the ensemble, and
postprocessing is on because the documented workflow runs
`nnUNetv2_find_best_configuration` between training and inference. Each is a
program argument, so a program that wants something else says so where the
reader can see it -- one fold, or the raw network output.

**Several configurations.** Naming a list is the documented workflow: nnU-Net
preprocesses each configuration, trains each (with `--npz`, so softmax is kept),
and then `nnUNetv2_find_best_configuration` scores them on the cross-validation
and names the single model — or the ensemble of two — that did best. The model
handle carries that selection, and `predict` runs it: for an ensemble it averages
the **probabilities**, which is how nnU-Net ensembles, and not the labels.

The cost is roughly one training per configuration. Naming one configuration
costs exactly what it did before, asks nobody, and writes no softmax.

**Starting weights.** `pretrained` maps to `nnUNetv2_train -pretrained_weights`.
The checkpoint must come from a model with the same `plans` and `configuration`,
or nnU-Net refuses to load it. The path is resolved to an absolute one and
checked before any preprocessing runs. It is an argument, not a setting: two
models trained on the same data from different starting weights are two
different models, and the cache key has to say so.

**Postprocessing.** After training, nnU-Net measures its own validation
predictions with and without keeping only the largest connected component, and
keeps the variant that scored better. `train` asks that question once, caches
the answer where nnU-Net caches it (`postprocessing.pkl` beside the validation
predictions), carries it on the model handle, and `predict` applies it. Often
the answer is "raw output is best", which is a real answer and costs nothing.
A model trained before this existed gains the answer the first time it is used
to predict, without being retrained.

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
nnunet.make_predictor(model, [device], [folds], [step_size], [tta], [checkpoint])
```

| # | Name | Required | Description |
|---|------|----------|-------------|
| 0 | `model` | yes | Handle returned by `nnunet.train` |
| 1 | `device` | no | `"cpu"` or `"cuda"`. Default: device stored on the model handle |
| 2 | `folds` | no | Folds to use. Default: folds trained in the model handle |
| 3 | `step_size` | no | Sliding-window step. Default: `0.5`; must be in `(0, 1]` |
| 4 | `tta` | no | Test-time augmentation by mirroring. Default: `"true"` |
| 5 | `checkpoint` | no | Checkpoint file name. Default: `"checkpoint_final.pth"` |

These are nnU-Net's own inference options (`-step_size`, `--disable_tta`, `-chk`)
with nnU-Net's own defaults, so saying nothing keeps the documented behaviour.
They are carried on the handle, not just used when it is built: a handle is a
value the engine persists and rebuilds from in a later process, and a knob kept
elsewhere would revert to the default exactly then.

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
