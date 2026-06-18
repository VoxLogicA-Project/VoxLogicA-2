# nnUNet namespace

Sequence-based training and handle-based prediction for nnU-Net v2.

## API

```voxlogica
model = nnunet.train(training_cases, work_root, modalities, configuration, nfolds, dataset_name, device)
predictions = nnunet.predict(model, prediction_cases)
status = nnunet.env_check()
```

### Training case

`[case_id, [modality_volume, ...], label_volume]`

### Prediction case

`[case_id, [modality_volume, ...]]`

### Model handle

`nnunet.train` returns an opaque mapping with `vox_kind = "nnunet_model"`. Pass it directly to `nnunet.predict`.

## Layout

```
work_root/
  nnUNet_raw/
  nnUNet_preprocessed/
  nnUNet_results/
  voxlogica_manifest.json
```

## Environment

- `nnunetv2` and `torch` must be installed.
- Optional `VOXLOGICA_NNUNET_TRAINER` selects a custom nnU-Net trainer class for faster test runs.
