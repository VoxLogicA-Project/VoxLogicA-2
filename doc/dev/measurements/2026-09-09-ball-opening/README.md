# 42% faster, and not the same computation: a method choice, not an optimisation

**Target.** `smoothen(a,x) = distleq(x, distgeq(x, !(a)))` is a morphological
opening written as two distance transforms, so every call costs two, and
`segment` calls it twice per (case, threshold). `dt` is still 47% of the sweep's
kernel time after the squared-distance change. One ITK binary opening by a ball
would replace both transforms.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
20 BraTS2020 cases, 16 goals, `--threads 32`, cold store per run, host
`fmt-5000`, 24 cores idle. Baseline `d7ebd22` (squared distance). Three
repetitions.

## Result: it is much faster, and it computes something else

| formulation | wall (3 reps) | mean | CPU | kernels | recomputes | **Dice** |
|---|---|---|---|---|---|---|
| two distance transforms (`d7ebd22`) | 26.5, 27.0, 27.7 | **27.1 s** | 1844% | ~14,860 | 252–564 | **0.8513501430954795** |
| one ball opening | 16.4, 16.4, 16.5 | **16.4 s** | 1825% | ~8,100 | 32–60 | **0.861210078655466** |

−42% of the wall clock, 45% fewer kernels, CPU flat — and a different answer.

**Why they differ, measured rather than guessed.** On one case at radius 2, with
a cold store:

```
region                  160,167 voxels
distance-based opening  158,646
ball opening            137,543      ⊂ the distance-based one (0 voxels outside it)
difference               21,103
```

The ball opening is anti-extensive, so it is a genuine opening; it simply erodes
one shell further. The discrete ball of radius r contains the point at distance
exactly r, so ball erosion keeps a voxel only where `d(v, complement) > r`,
while `distgeq` asks `d >= r`. A strict-versus-non-strict boundary at exactly
the radius — and `compat.imgql` already contains both conventions, since
`imopen(a,x) = dilate(erode(a,x),x)` uses the strict one and `smoothen` does
not.

**Disposition: reverted.** The standing rule for this work is that a change
which moves the Dice is a change of method and must be reported rather than
kept, and this moves it by 0.0098 — which is also, by this project's own
convention, inside the noise for twenty cases and therefore not evidence that
either convention is better. What it would do for certain is move every
published number in the paper. That decision is the experiment's owner's, not a
performance pass's. `ball_opening` remains as a primitive for anyone who wants
that convention deliberately.

## A methodological trap worth recording

The first comparison of the two openings returned IDENTICAL numbers before and
after a bug fix in the kernel. The store had served the old value: **node
identity is the hash of the expression, and the kernel's implementation is not
part of it**, so editing a kernel does not invalidate anything the store already
holds. Every measurement of a kernel change must therefore run against a cold
store — the sweep scripts here already delete theirs per run, which is why those
numbers stand, but the one-case comparison did not and had to be redone.

(The bug that hid behind it: `sitk.BinaryMorphologicalOpening`'s fourth
positional argument is `backgroundValue`, not `foregroundValue`. Passing 1.0
there declared 1 to be background and inverted the image, yielding an "opening"
of 311,499 voxels from an input of 160,167 — larger than its own input, which an
opening cannot be. That impossibility is what caught it, not the Dice.)
