# Four engines, one answer

VoxLogicA has four evaluators in play, and until this kit nothing compared what
they COMPUTE. The existing `perf/scaling/vl1_comparison` measures how long
VoxLogicA 1 and 2 take; it says nothing about whether they agree.

| | engine | how it is invoked |
|---|---|---|
| **A** | VoxLogicA 1 (1.3.3-experimental) | the binary, on a `.vl1.imgql` |
| **B** | VoxLogicA 2, lazy strategy | `--no-engine`, on the same checkout as C |
| **C** | VoxLogicA 2, scheduling engine | the default on `incoming` |
| **D** | VoxLogicA 2, engine with handles | the default on `handles` |

A takes its own syntax, so every case is a PAIR of programs that are meant to
compute the same thing. That is the kit's one weakness and it is stated up
front: a disagreement between A and the others can be a divergence between the
engines OR a mistranslation between the two languages, and the pair does not
know which. B, C and D share a syntax, so a disagreement among THEM is
unambiguous -- it is a bug.

So the cases are ordered by how much they can prove:

1. `arithmetic` -- numbers only. Any disagreement anywhere is a defect.
2. `volume_threshold` -- one number off one real volume. Same.
3. `tacas19` -- the published whole-tumour recipe. Here a mistranslation is
   possible, so a disagreement is a question rather than a verdict.

## Running it

```bash
python tests/differential/run_differential.py
```

It uses whatever it can find and says what it skipped: engines are located by
path, not assumed. Nothing here is part of the fast suite -- it needs three
checkouts, a .NET binary and the BraTS data.

## First full run — 2026-09-04, fmt-5000

Target: do the four evaluators compute the same thing? Inputs: BraTS2020
case 001 (`smoothing`), cases 001-003 (`tacas19`), literals (`arithmetic`).
Method: same program per dialect, goals compared numerically at 1e-9.

| case · goal | A vl1 | B lazy | C engine | D handles |
|---|---|---|---|---|
| arithmetic · sum, prod, nested | 3, 12, 30 | 3, 12, 30 | 3, 12, 30 | 3, 12, 30 |
| volume_threshold · brain | 1342885 | *parse error* | 1342885 | 1342885 |
| volume_threshold · hot | 66856 | *parse error* | 66856 | 66856 |
| smoothing · raw | 66856 | 66856 | 66856 | 66856 |
| smoothing · smoothed5 | 23098 | **0** | 23098 | 23098 |
| smoothing · smoothed2 | 48219 | **0** | 48219 | 48219 |
| tacas19 · dice case 1 | 0.8663475533 | **0** | 0.8663475533 | 0.8663475533 |
| tacas19 · dice case 2 | 0.8249720707 | **0** | 0.8249720707 | 0.8249720707 |
| tacas19 · dice case 3 | 0.4213056030 | **0** | 0.4213056030 | 0.4213056030 |

**A, C and D agree on every goal**, to VoxLogicA 1's printed precision. The
content-addressed engine and the handles engine both reproduce the reference
implementation exactly, including the TACAS'19 Dice.

**B returns 0 wherever a distance transform appears** — an empty mask, printed
as a number, with no error. `main`'s lazy strategy hands `dt` a Python `bool`
instead of an image (`vox1/kernels.py`, `_as_image`); on the smoothing path it
degrades to an empty region rather than raising. A silent wrong answer is worse
than the crash it nearly was, and no non-differential test could see it.

## Second full run — 2026-09-24, fmt-5000

Same four cases, same data, on today's branches: A the same VL1 binary,
B `--no-engine` on `main`, C `incoming`, D `handles`.

| case · goal | A vl1 | B lazy | C engine | D handles |
|---|---|---|---|---|
| arithmetic · sum, prod, nested | 3, 12, 30 | 3, 12, 30 | 3, 12, 30 | 3, 12, 30 |
| volume_threshold · brain | 1342885 | 1342885 | 1342885 | 1342885 |
| volume_threshold · hot | 66856 | 66856 | 66856 | 66856 |
| smoothing · raw | 66856 | 66856 | 66856 | 66856 |
| smoothing · smoothed5 | 23098 | 23098 | 23098 | 23098 |
| smoothing · smoothed2 | 48219 | 48219 | 48219 | 48219 |
| tacas19 · dice case 1 | 0.8663475533 | 0.8663475533015994 | same | same |
| tacas19 · dice case 2 | 0.8249720707 | 0.8249720707103897 | same | same |
| tacas19 · dice case 3 | 0.4213056030 | 0.4213056029987455 | same | same |

**Eleven goals, four evaluators, zero disagreements.** The two defects the first
run exposed are both gone: the lazy strategy no longer returns 0 through a
distance transform, and `volume_threshold` no longer fails to parse for it.

Two things changed in the kit to make this run possible, and both say something
about what the kit measures. `main` used to BE the lazy evaluator; it is now the
engine, and it rejects the `vl2main` dialect the kit fed it (`border` takes an
argument now), so every B goal came back missing and the column read as four
disagreements that were really four parse failures. The lazy strategy is
therefore reached where it now lives, `--no-engine` on the same checkout and the
same dialect. And `PYTHON_GIL=0` is now set explicitly: this runner bypasses the
`./voxlogica` wrapper, and without it SimpleITK re-enables the GIL at import,
comparing the engines under a runtime none of them is meant to run on.

Two lessons are baked into the kit. Goals are parsed with an anchored regex,
because reading traceback lines as goals once made a crashed engine look like it
agreed. Disagreements name the dissenting engine, because a bare DIFF beside
three matching numbers reads as a comparator artefact.
