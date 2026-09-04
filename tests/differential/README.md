# Four engines, one answer

VoxLogicA has four evaluators in play, and until this kit nothing compared what
they COMPUTE. The existing `perf/scaling/vl1_comparison` measures how long
VoxLogicA 1 and 2 take; it says nothing about whether they agree.

| | engine | how it is invoked |
|---|---|---|
| **A** | VoxLogicA 1 (1.3.3-experimental) | the binary, on a `.vl1.imgql` |
| **B** | VoxLogicA 2, lazy strategy | a `main`-era checkout, or `--no-engine` |
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
