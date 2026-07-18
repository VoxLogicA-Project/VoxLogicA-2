# Handover: implement Phases 1–4 of semantic-queueing fusion

Read `doc/specs/semantic-queueing-fusion.md` first — it is the design of
record. This file is the concrete build order, the exact scheduler hook points
(already scouted), the locked decisions from design review, and the
per-commit gates. Implement all four phases, each as its own commit on
`feat/pointwise-fusion`, then stop for review. Do **not** merge to `incoming`.

Working dir: repo root. Branch: `feat/pointwise-fusion` (already checked out).
Python: `.venv/bin/python`, always `PYTHONPATH=implementation/python`.
Run tests with `-p no:cacheprovider`. `pytest.ini` sets `--maxfail=1`; override
with `--maxfail=10000 --continue-on-collection-errors` to see the whole picture.

## Ground truth: what Phase 0 already did (do not redo)

- `voxlogica/arrays.py`: `PolyArray` (lazy cached sitk/np views, `geometry`,
  `nbytes`, `is_readonly_np`, DLPack) and `Geometry`. Committed `4d64ff1`.
- `engine/executor.py`: `_unwrap`/`_wrap` — the sole adapter boundary. Kernel
  inputs that are `PolyArray` → `.sitk()`; kernel `sitk.Image` results →
  `PolyArray.from_sitk`.
- `engine/node_table.py::load`, `value_model.py`, `engine/strategy.py::_materialize`:
  live tier is uniformly `PolyArray`; persistence byte-identical; goal outputs
  unwrapped back to `sitk.Image`. Committed `4d64ff1` + `641e592`.
- The live tier holds `PolyArray` for every image value. Assume this.

## Pre-existing broken tests (NOT yours — ignore, do not "fix")

These fail on clean `feat/pointwise-fusion` before any of your work, from
dead-module refs unrelated to fusion. Baseline them once so you can tell your
regressions from theirs:

- collection errors: `test_execution_trace`, `test_inspectable_sequence`,
  `test_serve_support`, `test_main_entrypoints`, `test_parser_operator_compat`,
  `test_policy`, `test_unsupported_value_policy`, `test_vox1_operator_overloads`,
  `tests/contract/test_strategy_contract`.
- failures: `test_default_primitives::{test_sequence_arithmetic_overloads,
  test_dask_arithmetic_overloads,test_range_primitive}`,
  `test_delete_cache_cli::test_delete_cache_prompt_confirmed`,
  `test_parallel_execution_strategy::*`, all of `tests/regression`.

Your bar: **111 passing unit tests stay passing**, plus your new tests.

---

## The scheduler hook points (already scouted — use these exact seams)

**Pop / dispatch** — `engine/core.py::_worker`, the final `else` at
`core.py:591–619`. This is where a popped ready node with a real kernel is
about to run `await self.executor.run(self.table, nid)`. Fusion plans a cone
seeded at `nid` **here**, immediately before that dispatch. Everything above
this branch (aliases, already-materialized, expandable loops, constants,
closures) is *not* fusable and must fall through unchanged.

**Graph state available at pop time** (`engine/graph.py`, all O(degree) dict
lookups, no traversal):
- `graph._dependents[nid]` → list of registered consumers of `nid`.
- `graph.deps(nid)` → frozenset of a node's dependency ids.
- `graph.pending[nid]` → unmet-dep count (absorbable interior has ≥1; a
  queue-resident node has 0 — this is the §3.3 "members are never
  queue-resident" invariant, assert it).
- `graph.consumers[nid]` → refcount of unrun consumers (for output selection).
- `table.completed` (set), `table.values` (id→PolyArray/value), `self._goals`
  (set of goal ids).
- Claim a member with `table.begin(member)` (raises `DoubleComputationError`
  if already running/materialized — catch and drop that member from the cone).

**Completion** — `engine/core.py::_finish(nid, value, persist=, compute_ms=)`.
Call it once per cone member, in **topological order**. It does
`table.complete` (set_value + persist + `complete_item` for sequences), fires
dependents via `graph.on_complete` → `_enqueue`, updates eviction candidates,
progress, and `_settle_node`. A cone member that gets fired-and-enqueued by an
earlier member's `_finish` is caught by the `nid in self.table.completed:
continue` guard at `core.py:559` when a worker later pops it — so correctness
holds even without filtering, but prefer to skip enqueuing known cone members
to avoid queue churn (pass the member set down, or filter the fired list).

**Metrics** — extend the dict in `core.py::metrics()` (`core.py:684`). Add:
`cones_dispatched`, `ops_fused` (members absorbed beyond the seed),
`mean_cone_size` (track sum+count), and in Phase 2 `kernel_cache_hits/misses`,
`stage_a_fallback_dispatches`. They surface automatically in bench JSON.

**Config / kill switch** — `engine/config.py`, follow the existing
`_env_int`/`from_env` style. Add `fusion_enabled` (`VOXLOGICA_FUSION`, default
1), `fusion_cap` (`VOXLOGICA_FUSION_CAP`, default 64), and (Phase 2)
`fusion_stage` (`VOXLOGICA_FUSION_STAGE`, `a`|`b`) and `fusion_outputs`
(`VOXLOGICA_FUSION_OUTPUTS`, `needed`|`all`). Every commit must be a no-op when
its switch is off — that is what lets us bench every version from its commit.

---

## Phase 1 — ElementwiseSpec registry + FusionPlanner + Stage A

Goal: kill the per-node GIL dispatch tax by running a ripe cone of existing
kernels in **one** thread-pool task per pop, instead of one async round trip
per node. **No numba yet.** Stage A changes *scheduling*, not *math* — each
member still runs its real sitk kernel, so results are bit-identical by
construction and geometry propagates for free (sitk kernels copy it).

### 1a. Elementwise opt-in metadata
- Add `elementwise: ElementwiseSpec | None = None` to `PrimitiveSpec`
  (`primitives/api.py:69`). `ElementwiseSpec` = `{expr: str, out_dtype: str,
  commutes_scalar: bool = True}` (spec §2). Stage A does not read `expr` yet
  (Stage B does) — but declare it now so the fusable set is defined in one place.
- Mark fusable in `vox1/__init__.py` (the `_PRIMITIVES` dict, then
  `register_specs`): `and, or, not, leq_sv, geq_sv, between`. Add arithmetic
  (`+ * / -`) **only** if a property test proves bit-identical output vs the
  sitk kernel including type promotion — otherwise leave out (spec §2 rule:
  when in doubt, exclude). A kernel is "fusable" iff its spec has
  `elementwise` set and it is not a goal.
- `default.index` / `default.sequence` / `subsequence` / any sitk morphology,
  distance, connected-components, resample op: never elementwise (spec §2).

### 1b. FusionPlanner — `engine/fusion.py` (new)
Pure, testable, no async. `plan(seed, *, graph, table, goals, cap) -> Cone | None`
implementing spec §3.0–3.1:
- Fusable seed only, else return None (worker takes the normal path).
- Grow **downward** into consumers (`graph._dependents`) that are (a) fusable,
  (b) **ripe**: every dep is in the cone or in `table.completed`, (c) same
  shape as the seed (check materialized input `.shape`; cut on mismatch),
  (d) claimable (`table.begin`, catch `DoubleComputationError`). Fixpoint,
  capped at `cap`.
- `< 2` members after growth → unclaim, return None.
- Classify: member is **interior** iff every consumer is in the cone and it is
  not a goal; else **exit**. Compute a topological order of members.
- Return `Cone(members_topo, inputs, exits, interiors)`. Assert no member had
  `pending == 0` at claim (they must not have been queue-resident).

### 1c. ConeRunner — one pool task, all members
Add `Executor.run_cone(table, cone)` (async, mirrors `run`): offloads to the
pool **once** and, in the pool thread, loops members in topo order:
- gather each member's inputs from a **local scratch dict** (interior inputs
  produced earlier in this cone) falling back to `table.values` (external
  inputs, unwrapped via the existing `_unwrap`);
- run the member's real kernel (reuse `_compute`'s invoke logic, but sourcing
  args from the scratch, not the table — factor a helper so there's no
  divergence);
- store the raw result in the scratch; if the member is an exit, wrap it
  (`_wrap`) for return.
Return `{member_id: value}` for **exits** in Phase 1 (Stage A materializes
interiors in scratch but need not surface them — but see below).

**Phase-1 output policy = materialize everything** (equivalent to
`FUSION_OUTPUTS=all`): return every member's value, not just exits. Rationale:
interior elision requires the recompute fallback, which is Phase 2. Returning
all is never worse than today (unfused already materialized each node) and
sidesteps the late-hash-consing hole entirely (spec §3.2). Keep it simple and
correct first.

### 1d. Wire into `_worker`
In the `core.py:591` else-branch, before the existing single-node dispatch:
```
if self.config.fusion_enabled:
    cone = self.fusion.plan(nid, graph=self.graph, table=self.table,
                            goals=self._goals, cap=self.config.fusion_cap)
    if cone is not None:
        results = await self.executor.run_cone(self.table, cone)
        for member in cone.members_topo:      # topo order
            self._finish(member, results[member], compute_ms=<share>)
        # update metrics; continue the worker loop
        ...
        continue
# else: existing single-node path unchanged
```
`begin()` was already called by the planner for every member (claim). The
seed: note the normal path calls `begin(nid)` itself — make sure the seed is
claimed exactly once (planner claims it, or planner excludes the seed from its
own begin and the worker keeps its begin — pick one, assert no double-begin).
Divide cone wall-time across members for `compute_ms` (approximate; document).

### 1e. Tests (gates)
- **Equivalence**: for random small volumes and random ripe cones over the
  fusable set, fused (`VOXLOGICA_FUSION=1`) vs unfused (`=0`) → `np.array_equal`
  + dtype match on every goal. This is the critical gate.
- **Planner invariants**: goals never interior; a node with an external
  consumer is an exit; growth cut on shape mismatch / non-elementwise /
  unclaimable; members never queue-resident (pending≥1).
- **Bookkeeping**: after a fused run, `graph.pending/consumers/incomplete`
  empty exactly as after an unfused run; `kernels_executed` accounting sane.
- **Warm-run**: fused cold then warm reuses (kernels≈0); fused cold then
  `VOXLOGICA_FUSION=0` warm is correct.
- Full 111-test suite stays green; one real `.imgql` (e.g.
  `tests/brats_brain_tumour_segmentation.imgql` shape, or a small synthetic)
  compared on vs off → identical printed output.
- **Bench**: `tests/perf/bench_scheduler.py --elements 150 --width 1000
  --rounds 4 --json` before/after. Note: the bench's `test.blob` returns numpy,
  not sitk, so it will NOT form cones — it only proves no regression. To prove
  the win you need an elementwise-heavy sitk program; add a small perf program
  or measure via the `.imgql`. Record nodes/s, mean_busy_cores, and the new
  cone metrics to `scratchpad/`. Report honestly.

Commit: `engine: Phase 1 — schedule-time cone fusion (Stage A)`.

---

## Phase 2 — Stage B: numba codegen + CONE-LEVEL COMPLETION + output selection

REVISED after Phase 1's measurement (see frontier-scheduler.md "Semantic
queueing"): mean cone size 9.0 bought only 1.1–1.3× because the per-node
floor is `_finish`'s bookkeeping (`on_complete`→`release` per member), which
Stage A deliberately kept per-member. Numba alone would NOT fix that — the
kernels are already cheap. Phase 2 therefore has TWO legs, and the second is
the one that removes the floor:

### 2-leg-1: cone-level batched completion (`graph.complete_cone`)

New method on `DependencyGraph`, called once per cone instead of k
`_finish`/`on_complete` rounds. For a cone with members M, interiors I,
exits E, external inputs D:

- **Batched input release**: for each d ∈ D, decrement `consumers[d]` by the
  number of cone members consuming d — ONE dict update per distinct input,
  not one per (member, input) edge. Evict on zero as `release` does.
- **Interiors** (all consumers in-cone, not goals): mark
  `table.completed.add(i)`, drop `incomplete/pending/_dependents/_deps_memo`
  entries. NO `set_value`, NO persist decision, NO evict-candidate queueing,
  NO per-node progress tick (add len(I) to the progress counter in one add).
  In-cone consumer refcounts between members are never even created — the
  cone claims its members before any of them registers a hold... they were
  already registered (consumers counts exist) — so decrement them in the
  same batch, symmetric with D.
- **Exits**: full normal `_finish` each (value, persist policy, candidates,
  settle, fired-dependent enqueue — minus in-cone members, as Phase 1's
  `skip_enqueue` already does).
- Fired external dependents are collected across the whole batch and
  enqueued once at the end.

Invariants to test: after a fused run, `pending/incomplete/_dependents/
consumers` are exactly as empty as after an unfused run; progress totals
identical; an interior is never persisted (consistent with today's
`persist_min_compute_ms` skipping cheap values); `registered_total`
accounting unchanged.

This leg lands FIRST and is measurable with Stage A execution alone
(interior values still computed in scratch, just never `set_value`d): the
tiny-image bench (`fusion_bench_tiny.py`, scratchpad) should move well past
1.3× if the diagnosis is right. If it doesn't, STOP and reprofile before
touching numba.

**Correctness prerequisite (unchanged from spec §3.2):** an elided interior
can later be demanded by a hash-consed consumer from a future expansion
chunk. Implement the recompute fallback in `_rematerialize` (value neither
live nor on disk → re-execute the spec, recursing on its deps) BEFORE
enabling elision; gate with the late-consumer test.

### 2-leg-2: numba codegen (as originally specced)

Spec §3.2b. Only after leg 1's numbers are recorded.

- **Backend: numba** `@njit(nogil=True, cache=True, parallel=False)`. Generate
  one kernel per cone **shape** from the `ElementwiseSpec.expr` fragments,
  computing the whole cone in one loop nest and storing the **selected output
  set** (spec §3.2). numexpr is explicitly rejected (single-output).
- **Output selection**: with `FUSION_OUTPUTS=needed` (new default once the
  fallback below exists), write exits + interiors with a consumer outside the
  cone; elide the rest. `=all` stays as the safe/debug mode and Phase-1 behavior.
- **Compile off the pop path**: per-shape state machine
  `UNCOMPILED → COMPILING → READY` (spec §3.2b). Shape key = canonical
  expression (ops, arity, dtypes, scalars-as-params) **+ output mask**. While
  not READY, that shape's cones run **Stage A** (already built). Compile in a
  background thread; `cache=True` persists machine code across runs. LRU
  cache of compiled callables. Metric the hit/miss and fallback counts.
- **Geometry propagation (required here)**: Stage B outputs are numpy →
  `PolyArray.from_numpy(arr, geometry)`. The geometry must be inherited from
  the cone's image inputs (they share shape/geometry by the §3.1 shape guard),
  NOT left identity. Assert geometry round-trips in a test.
- **Recompute fallback** in `core.py::_rematerialize`: value neither live nor
  on disk → re-execute its spec (elementwise ⇒ cheap; recursion bottoms out at
  live/persisted). Closes the late-hash-consed-consumer-of-an-elided-interior
  hole (spec §3.2). Gate with a test that constructs exactly that situation
  (a second `reduce_chunk` hash-consing onto an elided interior).
- **Bit-identical property tests again**, Stage B this time, incl. non-binary
  uint8 and float NaN inputs. Bench delta recorded.

Commit: `engine: Phase 2 — numba-compiled cones with output selection`.

---

## Phase 3 — Sites + affinity + numba.cuda retarget

Spec §4. `engine/sites.py`: `Site` protocol, `CpuSite` (wraps today's pool),
`GpuSite` (exists iff CUDA importable; else `available()` False). Placement
heuristic in **one** logged function (spec §4). Retarget the Phase-2 codegen
to `numba.cuda` — same generated source, same output selection. Residency +
transfer tracked on `PolyArray` (add a `"cuda"` view + `to(site)`), counted in
metrics. **CI must pass with and without CUDA** (all dev Macs have none — the
GPU path must be entirely inert there). No `RemoteSite` — design constraint
only (the `Site` protocol must not assume shared memory), not a deliverable.

Commit: `engine: Phase 3 — execution sites + GPU cone backend`.

---

## Phase 4 — buffer pool + docs + bench write-up

Spec §5 (pool), §7. Per-`(site, shape, dtype)` free-list with a byte cap;
Stage B writes through `out=` into pooled buffers; last-release returns
fusion-produced buffers to the pool (never sitk-owned ones). Metric
`pool_hits`, `pool_bytes`; a leak/stress test. Then write the "Semantic
queueing" section into `doc/dev/dynamic-scheduler/frontier-scheduler.md` with
the measured before/after per phase (honesty rules of the existing sections),
and the paper notes (the "semantic queueing" term, affinity as placement).

Commit: `engine: Phase 4 — buffer pool + fusion docs`.

---

## Hard invariants (all phases)

- Zero changes to admission / liveness / persistence *semantics* — fusion is a
  pop-time execution policy plus new kernels. Node ids, `hash_node`, warm-cache
  keys, hash-consing, serve/inspect addressing: untouched (the DAG is never
  rewritten).
- Bit-identical to fusion-off, enforced by property test, every stage.
- O(frontier)/O(degree) at pop time — never O(plan). No traversal in the planner.
- Every commit no-ops with its kill switch off, so each is independently
  benchmarkable from its own hash.
- House style: module docstrings stating invariants; no dead code; no
  speculative generality beyond the `Site` protocol.

Stop after Phase 4's commit. Report the four commit hashes and the per-phase
bench deltas. Fable reviews before anything merges or the 9M run is kicked off.
