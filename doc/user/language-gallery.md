# VoxLogicA Language Guide and Progressive Gallery

This document is a **narrative language guide**. Runnable gallery programs live in **[`doc/gallery/`](../gallery/README.md)** (`manifest.json` + `programs/<module>/<id>.imgql`).

Serve Studio loads those files via `GET /api/v1/docs/gallery` (see `voxlogica.gallery.load_gallery()`).

## 1. Core Syntax and Execution Model

Top-level declarations can use either `let` or bare assignment. Gallery examples use bare assignment.

| Example | Focus |
|---------|--------|
| [intro-hello](../gallery/programs/default/intro-hello.imgql) | Minimal scalar expression |
| [intro-arithmetic](../gallery/programs/default/intro-arithmetic.imgql) | Named scalar outputs |
| [intro-let-in](../gallery/programs/default/intro-let-in.imgql) | Local `let ... in ...` binding |
| [intro-symbolic-infix](../gallery/programs/default/intro-symbolic-infix.imgql) | Custom infix and uppercase names |
| [intro-prefix-unary](../gallery/programs/default/intro-prefix-unary.imgql) | Prefix unary calls |

## 2. Lazy Sequences (Default Module)

| Example | Focus |
|---------|--------|
| [default-range-map](../gallery/programs/default/default-range-map.imgql) | `range`, `map`, lazy sequences |
| [default-index](../gallery/programs/default/default-index.imgql) | `index` on tuple-like outputs |

## 3. SimpleITK Image Primitives

| Example | Focus |
|---------|--------|
| [sitk-threshold](../gallery/programs/simpleitk/sitk-threshold.imgql) | Read, threshold, statistics |
| [sitk-smooth](../gallery/programs/simpleitk/sitk-smooth.imgql) | CurvatureFlow smoothing |
| [sitk-threshold-sweep-overlay](../gallery/programs/simpleitk/sitk-threshold-sweep-overlay.imgql) | BraTS threshold sweep overlays |
| [sitk-brats-fixed-segmentation](../gallery/programs/simpleitk/sitk-brats-fixed-segmentation.imgql) | Fixed-threshold BraTS segmentation |

## 4. `vox1` Compatibility Module

| Example | Focus |
|---------|--------|
| [vox1-dot-ops](../gallery/programs/vox1/vox1-dot-ops.imgql) | Dotted operators and overloads |
| [vox1-cross-corr](../gallery/programs/vox1/vox1-cross-corr.imgql) | `crossCorrelation` chain |

## 5. Strings Module

| Example | Focus |
|---------|--------|
| [strings-format](../gallery/programs/strings/strings-format.imgql) | `concat` and `format_string` |

## 6. Arrays Module

| Example | Focus |
|---------|--------|
| [arrays-metrics](../gallery/programs/arrays/arrays-metrics.imgql) | Segmentation comparison metrics |

## 7. nnUNet Module

| Example | Focus |
|---------|--------|
| [nnunet-env](../gallery/programs/nnunet/nnunet-env.imgql) | Runtime availability check |
| [nnunet-circle-segmentation](../gallery/programs/nnunet/nnunet-circle-segmentation.imgql) | Train/predict with exported PNG previews |

## 8. Test Module

| Example | Focus |
|---------|--------|
| [test-demo-data](../gallery/programs/test/test-demo-data.imgql) | Structured synthetic payload |
| [test-enqueue](../gallery/programs/test/test-enqueue.imgql) | Workflow enqueue diagnostics |

## 9. End-to-End Progressive Example

| Example | Focus |
|---------|--------|
| [progressive-end-to-end](../gallery/programs/mixed/progressive-end-to-end.imgql) | `simpleitk`, `strings`, and `vox1` combined |
