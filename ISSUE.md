# ISSUE (RESOLVED 2026-07-06): `DoubleComputationError` on warm-cache runs with shared subexpressions

> **RESOLVED** in `incoming` `64da55e` ("forward already-materialized nodes instead of
> recomputing"), merged `f0a04c0`. Verified: the minimal repro passes, and the real
> workload (cold `brats014` --store-db → warm `brats015`) now completes with **crash=0**,
> `cache_hits=2964`, oracle **byte-identical** (`avg_oracle_best=0.8713377621700489`).
> Kept below for the record.

---


**Branch:** `incoming` (observed at `57c9c03`)
**Component:** live engine scheduler + persistent-cache reuse path
**Severity:** crashes an otherwise-valid run; only on warm `--store-db` reuse (data-loss-free, but the run aborts with no results)
**Reproducibility:** deterministic-ish — triggers whenever a cache-resident node is *both* reloadable from disk *and* a shared dependency of ≥2 goals in the new run. Timing-sensitive (a race), so it may not fire on every workload/every run.

## Symptom

```
File ".../voxlogica/engine/core.py", line 407, in _worker
    self.table.begin(nid)  # enforces the no-double-computation invariant
File ".../voxlogica/engine/node_table.py", line 109, in begin
    raise DoubleComputationError(
        f"node {node_id[:12]} already {'running' if node_id in self._running else 'materialized'}")
voxlogica.engine.node_table.DoubleComputationError: node 3cf8e25346a8 already materialized
```

The offending node was **already materialized** (present in `NodeTable.values`), yet a
worker popped it from the ready queue and called `begin()` on it, which raises.

## How it was triggered (exact repro)

Context: iterating a segmentation sweep against a **persistent cache reused across runs**
(the intended workflow — cold-populate once, then run variants cheaply).

1. Cold-populate a DB with iteration N (`brats014.imgql`), which persists its
   goal-dependency cut + high-fan-out shared roots:
   ```
   cd looping_experiment
   REPO=/Users/vincenzo/data/local/repos/VoxLogicA-2
   PYTHONPATH=$REPO/implementation/python $REPO/.venv/bin/python -u -m voxlogica.main \
     run --store-db _scratch/reuse.db brats014.imgql
   ```
2. Run iteration N+1 (`brats015.imgql`) **against the same warm DB**:
   ```
   PYTHONPATH=$REPO/implementation/python $REPO/.venv/bin/python -u -m voxlogica.main \
     run --store-db _scratch/reuse.db brats015.imgql        # <-- crashes ~53 s in
   ```

`brats015.imgql` defines a shared sub-node **`bounded(h,f,r,g)`** consumed by *two*
goal families in the same run:
```
segM(h,f,r,g)    = fill_holes(maxvol(bounded(h,f,r,g)))     // identical to brats014's seg_b -> IN the cache
segO(h,f,r,o,g)  = fill_holes(imopen(bounded(h,f,r,g), o))  // new this run
```
Because `segM` (and thus the whole `bounded` subtree) is byte-identical to a node
brats014 already persisted, `bounded` is **cache-resident** for the new run, while also
being a **shared dependency of both `segM` and `segO`**. That combination is what fires it.

**`--no-cache` runs the identical workplan cleanly** (no disk tier to load from), which
isolates the fault to the cache-reuse + scheduling interaction, not the `.imgql`.

Note: the *previous* `brats015` (which shared `bounded` between `segM` and a
maxvol-free `segA`, no `imopen`) completed fine against the same warm DB — consistent
with this being a timing-dependent race that the new sweep's shape happens to expose.

## Root cause (analysis)

`NodeTable.begin()` (node_table.py:105) enforces single-computation:
```python
def begin(self, node_id):
    if node_id in self._running or node_id in self.values:
        raise DoubleComputationError(...)
    self._running.add(node_id)
```

The worker loop (core.py, ~line 396–410) checks *dependencies* are resident and handles
reload-deferral, then unconditionally calls `begin(nid)`:
```python
for dep in self._deps(nid):
    if dep not in self.table.values:
        self._rematerialize(dep)
self.table.begin(nid)      # <-- no re-check that nid itself is already in table.values
```
There is **no guard that `nid` itself hasn't become materialized** between the moment it
was enqueued and the moment the worker runs it. On a warm cache a node can enter
`table.values` via `NodeTable.load()` (disk reload) or be completed by another worker
that reached the same shared node through the other goal — after this node was already
admitted to the ready queue. The worker then calls `begin()` on an already-present node
and crashes. `--no-cache` never populates `values` from disk, so the window (almost)
never opens there.

In short: **ready-queue admission and cache-load/shared-completion are not
mutually exclusive** for a given node id; the single-computation guard is correct, but
the scheduler lets an already-satisfied node reach `begin()`.

## Suggested fix (for the implementor to choose)

- In `_worker`, right before `begin()`, **short-circuit if the value is already present**:
  ```python
  if nid in self.table.values:
      self._finish(nid, self.table.values[nid], persist=False)  # forward, don't recompute
      continue
  ```
  (mirrors how spliced/constant nodes are forwarded), and/or
- Have the ready-queue admission **skip / drop nodes already in `values`** (treat a
  cache hit as completion at enqueue time), and
- Make `begin()` on an already-materialized node a **no-op that signals "use cached
  value"** rather than raising — reserving `DoubleComputationError` for the genuine
  two-workers-same-node case (`nid in self._running`).

## Acceptance

- `run --store-db <warm brats014 DB> brats015.imgql` completes and is **byte-identical**
  to its `--no-cache` result.
- `cache_summary`: `recomputes ≈ 0`, `cache_hits > 0` (the shared `bounded`/preprocessing
  roots are reused, not recomputed), and no crash.
- A regression test: two goals sharing a subexpression that is present in a pre-populated
  `--store-db`, run warm, asserting completion + cross-run reuse.
