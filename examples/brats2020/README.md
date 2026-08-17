# BraTS2020, five cases

Whole-tumour segmentation on FLAIR alone — no training, no weights — scored
against the ground truth. Runs in about 5 seconds.

```bash
./voxlogica run examples/brats2020/sample.imgql
```

Open it in the UI instead, and each step becomes a card:

```bash
./voxlogica serve examples/brats2020/sample.imgql
```

## What it prints

Measured on this machine, 5 cases × 6 pool thresholds:

| case | best Dice | why this case is in the sample |
|---|---|---|
| 002 | 0.896 | compact, near the median size: the ordinary case |
| 079 | **0.000** | the smallest tumour in the dataset (7,285 voxels) |
| 089 | 0.652 | the most multifocal: 47% of the tumour is outside its largest piece |
| 230 | 0.958 | the median case (90,740 voxels), and the method's good behaviour |
| 328 | 0.913 | the largest tumour (361,783 voxels) |
| | **0.684** average | |

**Case 079 scoring zero is the point of including it.** The method seeds on the
top 5% of FLAIR intensity within the brain and grows from there; a tumour that
small may raise no seed at all, and then there is nothing to grow. That is not a
tuning problem — it is the method being inapplicable, and an example that only
contained cases it handles would be an advertisement rather than a sample.

## How the five were chosen

Every one of the 369 training ground truths was measured (voxel count, number of
connected pieces ≥100 voxels, and how much of the tumour lies outside its
largest piece). The five span a factor of 50 in size and include the extreme of
multifocality. Nothing was chosen for its score — the scores were computed
afterwards.

## The data

`data/` holds those five cases, gzipped, in the layout BraTS ships. It is
**not** in git (40 MB). To rebuild it from a local copy of the dataset:

```bash
for n in 002 079 089 230 328; do C=BraTS20_Training_$n; mkdir -p examples/brats2020/data/$C; for m in flair t1 t1ce t2 seg; do gzip -c "$BRATS/$C/${C}_$m.nii" > "examples/brats2020/data/$C/${C}_$m.nii.gz"; done; done
```

Point `dataset_root` in `sample.imgql` at the full dataset and nothing else
changes — the five are the same files, and the indices are folder order.
