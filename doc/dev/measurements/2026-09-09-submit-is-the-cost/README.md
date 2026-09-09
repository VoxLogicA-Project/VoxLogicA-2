# Inside the loop's biggest phase: `submit` is 94–96% of it, and `submit` is the copy

**Target.** `NodeTable.complete` is the event loop's largest phase and the tail
is loop-bound (`../2026-09-09-tail-is-loop-bound/`). `complete` does two things
— place the value in the live tier, and hand it to the persister — and which of
them costs decides what can be moved off the loop. Moving the wrong half has
already cost one experiment.

**Inputs.** The usual fixed mixed task:
`doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`, 20 cases, 16
of 16 goals, `dice_best_mean = 0.8513501430954795`, `--threads 32`, cold store,
host `fmt-5000` idle. `--measure-period 0.25 --measure-series`. Commit `82858ed`.
Run: 27.8 s wall, 1803% CPU, **502 CPU-seconds**, 33.07 CPU-ms per completion,
545 completions/s.

**Method.** Two counters inside `complete`, differenced across two windows of
the same run.

## Result

Saturated phase, 14.0 s of wall:

| phase | total | as a core | calls | per call |
|---|---|---|---|---|
| `fin_complete` (both) | 9.71 s | 69% | 4,998 | 1.943 ms |
| **`tbl_submit`** | **9.14 s** | **65%** | 3,052 | **2.995 ms** |
| `tbl_setvalue` | 0.54 s | 4% | 4,998 | 0.108 ms |
| `fin_oncomplete` | 0.41 s | 3% | 4,998 | 0.082 ms |

Export tail, 4.0 s of wall:

| phase | total | as a core | calls | per call |
|---|---|---|---|---|
| `fin_complete` (both) | 3.30 s | 82% | 2,696 | 1.222 ms |
| **`tbl_submit`** | **3.16 s** | **79%** | 2,643 | **1.194 ms** |
| `tbl_setvalue` | 0.13 s | 3% | 2,697 | 0.048 ms |

**`submit` is 94% of `complete` in the saturated phase and 96% in the tail.**
Placing the value in the live tier — the accounting, the buffer leases, the
completed set — is 3–4% and is not worth moving anywhere.

And `submit` does one expensive thing: `_payload_snapshot(value)`, a full copy
of the payload, taken because the buffer is ITK-owned memory that ITK frees on
its own schedule and a writer thread compressing the live alias reads unmapped
pages. The requirement is about ORDER — the copy must precede any later ITK call
that could free the buffer — not about which thread.

## Which corrects this morning's verdict twice over

`../2026-09-09-loop-copy-move/` moved that copy to the worker and called it
worthless. Both halves of that judgement were wrong:

- it was measured on the WHOLE-RUN average, and the saturated phase is 75% of
  the run and is not loop-bound, so a tail effect cannot appear there;
- the move as implemented held the copy alive between the worker and `_finish`,
  which raised memory pressure and recomputes 45%, and the extra recomputes ate
  the gain.

The mechanism was right; the placement and the measurement were not.

## The change this argues for

Submit from the WORKER, not from the loop. The worker already holds the value,
so the copy taken there is ordered strictly earlier than one taken on the loop,
runs on 32 threads instead of one, and — if it goes straight into the persist
queue rather than into a dict for `_finish` to collect — is never live outside
the queue, whose bytes are already budgeted. The loop is then left with the
graph bookkeeping it must serialise anyway: `on_complete`, the enqueue of fired
children, admission, priority — 0.41 s of 14 s in the saturated phase and 0.16 s
of 4 s in the tail.

What it needs, and what makes it more than a copy relocation: the persist
decision reads graph state (`critical` depends on a node's consumers), which a
worker thread must not race. That value is known at DISPATCH time, so it can be
computed on the loop and handed down with the node.

Predicted, before implementing: the tail loses ~3.1 s of the ~4.0 s of loop
occupancy that bounds it, so its CPU should rise from 800–1000% toward the
2100% the saturated phase reaches, and the tail should shorten from ~5.6 s
toward ~2.5 s — about 11% of the fixed task's wall clock. `cpu_seconds` must not
rise: the copy is the same copy, merely paid in parallel, so total CPU should be
flat and CPU-ms-per-completion should FALL. If `cpu_seconds` rises or recomputes
rise, the change is buying wall time with energy and is to be reverted.
