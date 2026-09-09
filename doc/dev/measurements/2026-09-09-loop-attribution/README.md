# Where the event loop's CPU goes: a payload copy, on the loop, per value

**Target.** The event loop was measured to be the ceiling of a large sweep
(`doc/dev/measurements/2026-09-09-io-and-loop/`): below 25% loop occupancy the
process ran 2250–2369% of 2400%, at ≥90% it collapsed to 791–1086%, monotonically
across twelve runs. "The loop is busy" is not something anyone can fix, so this
attributes the loop's own CPU to phases inside it.

**Inputs.** `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql` —
20 BraTS2020 cases, FLAIR only, 21-point threshold sweep, 16 goals. Host
`fmt-5000`, 24 cores, store on real disk under `/home/vincenzo/meas`,
`--threads 32`, 16 of 16 goals in both runs.

**Method.** `--measure`, plus `engine/measure.py::LoopClock`: the loop takes
`perf_counter_ns()` itself at each phase boundary and accumulates the delta into
a counter, which rides out in the per-sample snapshot. Cumulative counters, so a
reader differences consecutive samples — the same rule every other rate obeys.
Phases are whole-method where a method has several returns (a shim, so a phase
cannot be left open on one branch) and inline where the boundary is a statement.
No probe is created when nothing is measuring: the cost when off is one
`is not None` test per boundary.

Files: `clk_aiim.tsv` (worker-level phases), `fin_aiim.tsv` (the same run with
`_finish` broken down). Both complete, 16/16, `1937.3%` and `1922.8%` mean CPU.

## Result: one line of code is 65% of the loop

Cumulative totals at the end of a 26.8 s run, sorted:

| phase | total | calls | per call |
|---|---|---|---|
| **`NodeTable.complete`** | **17.55 s** | 10,758 | **1.63 ms** |
| `_finish` (the rest of it) | 11.14 s | 6,864 | 1.62 ms |
| `_rematerialize` of deps before dispatch | 1.55 s | 6,876 | 0.23 ms |
| `graph.on_complete` + enqueue of fired children | 0.98 s | 10,759 | 0.09 ms |
| bandwidth accounting | 0.08 s | 10,759 | 0.007 ms |
| `_reclaim_memory` | 0.05 s | 10,426 | 0.005 ms |
| `complete_item` over a sequence's elements | 0.03 s | 262 | 0.11 ms |
| `_await_named_deps` (the handle walk) | 0.02 s | 10,196 | 0.002 ms |
| evict-candidate / spill tracking | 0.02 s | 10,759 | 0.002 ms |
| `table.begin` + pin (dispatch proper) | 0.02 s | 6,876 | 0.003 ms |
| `hold_handles` | 0.01 s | 10,760 | 0.001 ms |
| alias forward | 0.01 s | 64 | 0.2 ms |

(`_finish`'s own total is smaller than `complete`'s because the outer timer
wraps only the single-node dispatch path, while the inner one covers the fused
cone path as well; the inner counts are the complete ones.)

Everything the scheduler is usually accused of — the handle walk, eviction
bookkeeping, enqueueing, dependency rematerialisation — is under one second
combined. **`NodeTable.complete` is 17.55 s of a 26.8 s wall clock**, on one
thread, and inside it the work is `AsyncPersister.submit`, whose last statement
is:

```python
# Snapshot HERE, on the event loop. See pod_codec.encode_for_storage:
# the payload aliases ITK-owned memory that ITK frees on its own
# schedule, so a writer thread compressing the live alias races a
# worker's SimpleITK call and reads unmapped pages.
self._queue.put((node_id, value, metadata, size, compute_ms, leases,
                 _payload_snapshot(value)))
```

`_payload_snapshot` copies the whole image payload off the ITK buffer. At 1.63 ms
per value that is about 8 MB copied per completion at memory speed, 10,758 times,
**serialised on the single thread that also dispatches every kernel**. The
comment is right about the hazard — a writer compressing a live ITK alias did
segfault — but the remedy is on the one thread that must not block.

## What follows

The copy is necessary; its *location* is not. It happens on the loop because
that is where `submit` is called, not because the loop is the only safe place:
the ordering requirement is that the copy precede any later SimpleITK call that
could free the buffer, and a copy taken on the WORKER thread that just produced
the value satisfies that strictly earlier than the loop does — while running on
32 threads instead of one.

Predicted effect if moved, stated before doing it: the loop loses ~17.5 s of
work per 27 s run, so loop occupancy should fall well below the 25% band where
the process was measured at 2250–2369%, and wall time should fall accordingly.
The copy itself does not disappear — it is paid in parallel by the workers, so
total CPU-seconds should go UP slightly while wall time goes down. If wall time
does not improve, the attribution above is wrong and this note says so.
