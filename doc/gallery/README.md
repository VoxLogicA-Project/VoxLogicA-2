# Example Gallery

Runnable VoxLogicA programs, ordered as a reading path rather than by module.
Every file is self-contained, commented, and prints or saves something — if a
program produces no visible output it does not belong here.

Run any of them from the repository root:

```bash
python -m voxlogica.main run doc/gallery/programs/<module>/<name>.imgql
```

## The idea in one paragraph

A VoxLogicA program is a set of **declarations**, not a sequence of steps. You
name expressions; nothing is computed until a `print` or `save` demands a value,
and then only what that value depends on. Identical subexpressions are the same
node in the graph and are computed once, whoever asked for them. This is why a
parameter sweep costs far less than the number of parameter combinations
suggests, and why programs read as definitions of *what* a result is rather than
instructions for producing it.

## 1 — Language basics

| Program | What it teaches |
|---|---|
| [`default/intro-hello`](programs/default/intro-hello.imgql) | Bind a name, print it |
| [`default/intro-arithmetic`](programs/default/intro-arithmetic.imgql) | Numbers, comparisons, single-assignment names |
| [`default/intro-let-in`](programs/default/intro-let-in.imgql) | Functions and `let … in` local bindings |
| [`default/intro-symbolic-infix`](programs/default/intro-symbolic-infix.imgql) | User-defined infix operators |
| [`default/intro-prefix-unary`](programs/default/intro-prefix-unary.imgql) | Prefix notation for unary operators |

## 2 — Sequences

| Program | What it teaches |
|---|---|
| [`default/default-range-map`](programs/default/default-range-map.imgql) | `range` and `map`: describe a whole result at once |
| [`default/default-index`](programs/default/default-index.imgql) | `index` into a multi-valued result |

## 3 — Images

| Program | What it teaches |
|---|---|
| [`simpleitk/sitk-threshold`](programs/simpleitk/sitk-threshold.imgql) | Call any SimpleITK filter; derive bounds from the image itself |
| [`simpleitk/sitk-smooth`](programs/simpleitk/sitk-smooth.imgql) | Write a volume to a computed path (`$stem`) |
| [`vox1/vox1-dot-ops`](programs/vox1/vox1-dot-ops.imgql) | Arithmetic lifted to whole images (`+.`, `.+`) |
| [`arrays/arrays-metrics`](programs/arrays/arrays-metrics.imgql) | Dice, Jaccard, pixel accuracy between two masks |
| [`strings/strings-format`](programs/strings/strings-format.imgql) | Labels and computed paths with `concat` / `format_string` |

## 4 — Spatial model checking

| Program | What it teaches |
|---|---|
| [`vox1/vox1-cross-corr`](programs/vox1/vox1-cross-corr.imgql) | Texture similarity; `percentiles` for per-image ranking |
| [`mixed/progressive-end-to-end`](programs/mixed/progressive-end-to-end.imgql) | Three namespaces in one program, formatted output |

## 5 — Real analyses

| Program | What it teaches |
|---|---|
| [`simpleitk/sitk-brats-fixed-segmentation`](programs/simpleitk/sitk-brats-fixed-segmentation.imgql) | The published BraTS recipe at fixed thresholds |
| [`simpleitk/sitk-threshold-sweep-overlay`](programs/simpleitk/sitk-threshold-sweep-overlay.imgql) | Sweep one threshold, produce an overlay per value |
| [`simpleitk/brats-threshold-sweep-aiim`](programs/simpleitk/brats-threshold-sweep-aiim.imgql) | **The full study**: per-case arg-best threshold, distribution statistics, worst-case export with and without ground truth |

## 6 — External tools

| Program | What it teaches |
|---|---|
| [`nnunet/nnunet-circle-segmentation`](programs/nnunet/nnunet-circle-segmentation.imgql) | Driving a trained network from a declarative program |

## Adding an example

One `.imgql` per file under `programs/<module>/`, where `<module>` is the
namespace it mainly exercises (`default`, `simpleitk`, `vox1`, `arrays`,
`strings`, `nnunet`, `mixed`). Add a row to the table above. Requirements:

- **It runs** from a clean checkout, against data in `tests/data/`.
- **It prints or saves.** A program with no output teaches nothing.
- **It is commented**, briefly, saying what the reader should notice — not
  restating the syntax.
- **It earns its place.** An example that only proves a primitive is callable
  belongs in the test suite instead.

Paths are relative to the repository root, so run programs from there.
