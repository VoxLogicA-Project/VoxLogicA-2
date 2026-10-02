# VoxLogicA 1 against VoxLogicA 2, same program, same constants

**Target.** Is VoxLogicA 2 faster than VoxLogicA 1, and if so is it because it
keeps more of the machine busy? The question was asked of the TACAS draft, which
claimed it without a measurement behind it.

**Why this is not the July comparison.** `tests/perf/scaling/vl1_comparison`
reported 1.08x, and its own README lists the reason it cannot do better: the two
sides ran *different recipes* (hi=0.95/vi=0.86/correction=0.5 against
hi=0.93/vi=0.88/correction=0), so only the operator pattern was comparable, not
the work. The differential kit's `tacas19x20` pair fixes exactly that: one
description generates both dialects, so the constants, the case list and the
operator sequence are identical, and the same kit proves the two sides compute
the same twenty numbers.

**Inputs.** `tests/differential/programs/tacas19x20.{vl1,vl2}.imgql`, BraTS2020
cases 1-20, host fmt-5000 (24 cores, load < 0.5), one process per run, page
cache warmed by an untimed run of each. VoxLogicA 1 is the 1.3.3-experimental
binary; VoxLogicA 2 is `f8333d6` with `--no-cache`, which is the setting that
matches VoxLogicA 1 (both memoise within the run, neither reuses a store).

## Result, three runs each

| | wall (s) | CPU | peak RSS |
|---|---|---|---|
| VoxLogicA 1 | 4.39, 4.36, 4.40 | 1135%, 1120%, 1121% | 16.6 GB |
| VoxLogicA 2 | 2.59, 2.56, 2.68 | 1622%, 1632%, 1598% | 4.7 GB |

**1.7x on wall clock, 1.4x on processor utilisation, 3.5x less memory**, for
twenty Dice scores that agree to the ten digits VoxLogicA 1 prints (the
differential run of the same day, `0 disagreement(s)` over 58 goals).

So the claim is true as stated: the engine is faster because it keeps more of
the machine busy, not because the kernels got faster -- and it does it from
Python against a .NET implementation that is itself parallel.

Variation across runs is under 5% on every figure, which is why three runs are
enough to report two significant digits and no more.
