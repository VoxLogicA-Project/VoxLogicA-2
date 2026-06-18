# nnUNet namespace

Sequence-based training, predictor handles, and per-image inference for nnU-Net v2.

## API

```voxlogica
model = nnunet.train(training_cases, work_root, modalities, configuration, nfolds, dataset_name, device, trainer)
predictor = nnunet.make_predictor(model)
segmentation = nnunet.predict(predictor, image)
status = nnunet.env_check()
```

### Training case

`[case_id, [modality_volume, ...], label_volume]`

### Model and predictor handles

- `nnunet.train` returns `vox_kind = "nnunet_model"`.
- `nnunet.make_predictor` returns `vox_kind = "nnunet_predictor"`.
- `nnunet.predict` returns a label image (SimpleITK).

### Training parameters

Pass `trainer` as the last `train` argument (default `nnUNetTrainer`). Use `nnUNetTrainer_10epochs` for short test runs.

## Layout

```
work_root/
  nnUNet_raw/
  nnUNet_preprocessed/
  nnUNet_results/
  voxlogica_manifest.json
```

## Requirements

`nnunetv2` and `torch` must be installed.
