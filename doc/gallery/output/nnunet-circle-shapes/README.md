# nnUNet circle-segmentation gallery output

Run from the repository root (set a short trainer for CPU demos):

```bash
VOXLOGICA_NNUNET_TRAINER=nnUNetTrainer_10epochs ./voxlogica run doc/gallery/programs/nnunet/nnunet-circle-segmentation.imgql
```

Generated artifacts:

- `training/` — PNG previews of each training image and label
- `predictions/` — test inputs and nnUNet segmentations as PNG (`nnunet.export_predictions`)

nnUNet working data lives under `/tmp/nnunet_circle_shapes_work`.
