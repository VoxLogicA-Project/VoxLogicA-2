# Parameter sweeps and arg-best provenance

## Scope

VoxLogicA parameter sweeps should keep the procedure, its independent parameter axes,
the score surface, and the per-case arg-best choice as separate symbolic values.

## Grid construction

`parameter_grid(axes)` returns the Cartesian product of a sequence of parameter axes.
Axis order is preserved and the rightmost axis varies fastest.

```imgql
hgrid = [0.93, 0.95, 0.97]
fgrid = [0.72, 0.83, 0.88]
rgrid = [12.0, 18.0, 999.0]
params = parameter_grid([hgrid, fgrid, rgrid])

case_score(g, p) = score_case(g, index(p,0), index(p,1), index(p,2))
surface(g) = for p in params do case_score(g, p)
argbest(g) = index(params, argmax(surface(g)))
best_score(g) = index(surface(g), argmax(surface(g)))
```

An empty axis produces an empty grid. Zero axes produce one empty parameter vector,
matching the mathematical Cartesian product identity.

## Saved output contract

Sweep programs use these print labels:

- `sweep_parameter_names`: ordered names matching every `argbest` vector.
- `sweep_*_names`: optional numeric-code legends such as channel and variant names.
- `gNN_score` or `cNN_score`: best per-case score.
- `gNN_argbest` or `cNN_argbest`: best parameter vector.
- `*_param_grid` and `*_surface_*`: optional grids and score surfaces for plotting.

`python -m voxlogica.sweep_artifact RUN.out` converts those prints to the stable
`voxlogica/sweep-results/v1` JSON artifact. A surface-only ImgQL program can be run
against the same persistent results database: because it reuses the same symbolic
score nodes, a warm export fetches cached scalars instead of recomputing image kernels.

## Acceptance criteria

- The case-analysis function accepts an explicit parameter vector.
- Grids are declared independently of the analysis function.
- Score and arg-best parameters are both saved for every reported case.
- Optional surfaces use the same score nodes as the accepted run.

## Example representation channel

`contralateral_asymmetry(image, sigma_mm)` is a deterministic Vox1 field for
atlas-oriented images. It smooths an image, reflects it across the left-right
index axis, and keeps the positive residual. It is intended as one oracle channel
alongside absolute intensity, not as a fitted classifier.
