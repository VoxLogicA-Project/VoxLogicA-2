# VoxLogicA Language Guide and Progressive Gallery

This document is both:

- a language reference with progressive examples
- the source for interactive playground cards in `voxlogica serve`

Playground cards are declared with markdown comments in this format:

```markdown
<!-- vox:playground
id: intro-hello
title: Minimal print
module: default
level: intro
strategy: strict
description: Short text shown in the gallery card.
-->
```imgql
print "hello" 1 + 2
```
```

## 1. Core Syntax and Execution Model

### Declarations, expressions, and goals

`let` introduces named expressions or functions. `print` and `save` are goals.

<!-- vox:playground
id: intro-arithmetic
title: Scalar arithmetic with print goals
module: default
level: intro
strategy: strict
description: Basic scalar evaluation and named outputs.
-->
```imgql
let a = 7
let b = 5
let c = a * b - 3
print "a_times_b_minus_3" c
print "is_gt_20" c > 20
```

### Local binding with `let ... in ...`

<!-- vox:playground
id: intro-let-in
title: Local let-in binding
module: default
level: intro
strategy: strict
description: Local substitutions with deterministic expression nesting.
-->
```imgql
let normalized(x,minv,maxv) = let span = maxv - minv in (x - minv) / span
print "scaled" normalized(42,10,50)
```

### Function declarations and infix operator names

Function names can be symbolic or uppercase and used infix.

<!-- vox:playground
id: intro-symbolic-infix
title: Symbolic/uppercase infix function names
module: default
level: intermediate
strategy: strict
description: Demonstrates custom infix operators and uppercase identifiers.
-->
```imgql
let SUM(a,b) = a + b
let +?(a,b) = a * b + 1
print "sum_case" 4 SUM 9
print "symbol_case" 5 +? 8
```

### Unary prefix calls

<!-- vox:playground
id: intro-prefix-unary
title: Prefix unary call
module: default
level: intermediate
strategy: strict
description: One-argument prefix call form.
-->
```imgql
let NEG(x) = 0 - x
print "negated" NEG 11
```

## 2. Lazy Sequences (Default Module)

### `range`, `map`, closures, and materialization

<!-- vox:playground
id: default-range-map
title: Lazy threshold sequence
module: default
level: intermediate
strategy: dask
description: Range and map produce lazy symbolic sequence plans.
-->
```imgql
let values = range(0,20)
let square(x) = x * x
let squares = map(square, values)
print "count" 20
print "squares" squares
```

### Indexed access from tuples/lists

<!-- vox:playground
id: default-index
title: Index primitive on tuple-like output
module: default
level: intermediate
strategy: strict
description: Extracting values from fixed-order structures.
-->
```imgql
import "simpleitk"
let img = ReadImage("tests/data/chris_t1.nii.gz")
let mm = MinimumMaximum(img)
print "min" index(mm,0)
print "max" index(mm,1)
```

## 3. SimpleITK Image Primitives

### Direct image processing pipeline

<!-- vox:playground
id: sitk-threshold
title: Binary threshold + statistics
module: simpleitk
level: core
strategy: strict
description: Read image, threshold, and inspect summary statistics.
-->
```imgql
import "simpleitk"
let img = ReadImage("tests/data/chris_t1.nii.gz")
let mm = MinimumMaximum(img)
let lo = index(mm,0)
let hi = index(mm,1)
let thr = BinaryThreshold(img, lo + (hi-lo)/3, hi, 255, 0)
let stats = Statistics(thr)
print "threshold_stats" stats
```

### Save derived outputs

<!-- vox:playground
id: sitk-save
title: Save derived NIfTI output
module: simpleitk
level: core
strategy: strict
description: Demonstrates deterministic save goals.
-->
```imgql
import "simpleitk"
let img = ReadImage("tests/data/chris_t1.nii.gz")
let smooth = CurvatureFlow(img, 0.15, 4)
save "tests/output/smoothed_chris_t1.nii.gz" smooth
print "saved" "tests/output/smoothed_chris_t1.nii.gz"
```

## 4. `vox1` Compatibility Module

### Dot operators and legacy-compatible morphology helpers

<!-- vox:playground
id: vox1-dot-ops
title: Vox1 dotted operators and overloads
module: vox1
level: advanced
strategy: strict
description: Uses vox1 .+/.*/./ variants and overloaded +/* forms.
-->
```imgql
import "simpleitk"
import "vox1"
let m = ReadImage("tests/data/chris_t1.nii.gz")
let i = intensity(m)
let a = i +. 4
let b = 4 .+ i
let c = (a *. 2) /. 3
print "vox1_overload_a" avg(a,tt)
print "vox1_overload_b" avg(b,tt)
print "vox1_overload_c" avg(c,tt)
```

### Vox1 cross-correlation workflow

<!-- vox:playground
id: vox1-cross-corr
title: Vox1 crossCorrelation chain
module: vox1
level: advanced
strategy: strict
description: Legacy-equivalent crossCorrelation and percentiles usage.
-->
```imgql
import "simpleitk"
import "vox1"
let m1 = ReadImage("tests/data/chris_t1.nii.gz")
let m2 = ReadImage("tests/data/chris_t1.nii.gz")
let a = intensity(m1)
let b = intensity(m2)
let c = crossCorrelation(2,a,b,tt,min(b),max(b),16)
let p = percentiles(c,40 .<= a,0.5)
print "vox1_corr_avg" avg(p,tt)
```

## 5. Strings Module

### `concat` and `format_string`

<!-- vox:playground
id: strings-format
title: Dynamic labels and paths
module: strings
level: core
strategy: strict
description: Use strings primitives to build labels or output paths.
-->
```imgql
import "strings"
let patient = "P-1024"
let score = 0.8732
let label = concat("patient=", patient)
let pretty = format_string("{:.2%}", score)
print "label" label
print "pretty_score" pretty
```

## 6. Arrays Module

### Segmentation array metrics

<!-- vox:playground
id: arrays-metrics
title: Array comparison metrics
module: arrays
level: advanced
strategy: strict
description: Pixel accuracy and overlap metrics using arrays namespace.
-->
```imgql
import "simpleitk"
import "arrays"
let img = ReadImage("tests/data/chris_t1.nii.gz")
let pred = BinaryThreshold(img,40,180,1,0)
let truth = BinaryThreshold(img,55,170,1,0)
print "pixel_accuracy" arrays.pixel_accuracy(pred,truth)
print "dice" arrays.dice_score(pred,truth,1)
print "jaccard" arrays.jaccard_index(pred,truth,1)
```

## 7. nnUNet Module

### Environment diagnostics before training/inference

<!-- vox:playground
id: nnunet-env
title: nnUNet runtime check
module: nnunet
level: expert
strategy: strict
description: Sanity check that nnUNet and torch runtime are available.
-->
```imgql
import "nnunet"
print "nnunet_env" nnunet.env_check()
```

### Directory-based training invocation

<!-- vox:playground
id: nnunet-train-directory
title: nnUNet train_directory template
module: nnunet
level: expert
strategy: strict
description: Skeleton for nnUNet training from directory datasets.
-->
```imgql
import "nnunet"
let images = "/data/my_dataset/imagesTr"
let labels = "/data/my_dataset/labelsTr"
let out = "/data/work/nnunet"
print "train_job" nnunet.train_directory(images,labels,out,1,"Dataset999","3d_fullres",5)
```

## 8. Test Module (Synthetic and Workflow Primitives)

### Structured synthetic payloads

<!-- vox:playground
id: test-demo-data
title: Structured result payload
module: test
level: core
strategy: strict
description: Test namespace primitive returning nested structured data.
-->
```imgql
import "test"
print "demo_payload" test.demo_data()
```

### Workflow enqueue primitives

<!-- vox:playground
id: test-enqueue
title: Workflow enqueue diagnostics
module: test
level: advanced
strategy: strict
description: Generate enqueue instructions used by workflow tests.
-->
```imgql
import "test"
print "enqueue_plan" test.enqueue("segment", "patient-42")
```

## 9. End-to-End Progressive Example

This compact flow chains `simpleitk`, `strings`, and `vox1`.

<!-- vox:playground
id: progressive-end-to-end
title: End-to-end imaging mini-workflow
module: mixed
level: advanced
strategy: strict
description: Imports multiple modules and combines image ops, formatting, and vox1 helpers.
-->
```imgql
import "simpleitk"
import "strings"
import "vox1"
let img = ReadImage("tests/data/chris_t1.nii.gz")
let i = intensity(img)
let mask = 60 .<= i
let corr = crossCorrelation(2,i,i,tt,min(i),max(i),16)
let avgCorr = avg(corr,mask)
let msg = concat("avgCorr=", format_string("{:.4f}", avgCorr))
print "summary" msg
```
