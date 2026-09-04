# BraTS2020, five cases

Whole-tumour segmentation on FLAIR alone — no training, no weights — scored
against the ground truth. Runs in about 5 seconds.

```bash
./voxlogica run doc/gallery/programs/brats2020/brats-five-cases.imgql
```

Open it in the UI instead, and each step becomes a card:

```bash
./voxlogica serve doc/gallery/programs/brats2020/brats-five-cases.imgql
```

## The same method, as a board you can read a case on

`brats-layers.imgql` is the same recipe with every step written as a *sequence
over the cases* instead of a function of one. That one change is what makes the
board work:

```bash
./voxlogica serve doc/gallery/programs/brats2020/brats-layers.imgql
```

Ten cards, nine of them carrying `index=g`. The chevrons on any of them rewrite
a single line of the program — `let g = 3`, visible in its own card at the
bottom left — and every card that mentions `g` follows. There is no link between
them to make or break: they share a name, which is all master and slave needs to
be.

The **Review** card shows three pictures at once (`[flairs[g], truths[g],
found[g]]`) with the ground truth in blue over the scan and the method's answer
in red over that. Where the two disagree is where the method is wrong, visible
without arithmetic. Take it apart with **⤴** on a row and each layer goes back
to being a card; drag a card onto another by its body to put them together
again. Colour, opacity and on/off live in the card's comment, never in the
expression — the expression is the cache key, so a slider must not be able to
recompute a volume.

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

## The board

The file carries its own layout, in comments, so `serve` opens a designed board
rather than a pile:

- **page 1** — the method, drawn. Six volumes in NiiVue: the scan, the seed, the
  pool, the result, the ground truth, and — the one worth looking at — the
  **disagreement**, everything the method found that is not tumour and
  everything it missed. A Dice of 0.958 says how much; only that card says
  *where*.
- **page 2** — the numbers, and the brain mask.

Press ▶ on a card to compute what that card is about; its dependencies follow on
their own, and every card showing one of them updates as it lands.

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
for n in 002 079 089 230 328; do C=BraTS20_Training_$n; mkdir -p doc/gallery/programs/brats2020/data/$C; for m in flair t1 t1ce t2 seg; do gzip -c "$BRATS/$C/${C}_$m.nii" > "doc/gallery/programs/brats2020/data/$C/${C}_$m.nii.gz"; done; done
```

Point `dataset_root` in `brats-five-cases.imgql` at the full dataset and nothing else
changes — the five are the same files, and the indices are folder order.
