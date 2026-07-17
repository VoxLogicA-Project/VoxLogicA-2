# Semantic queueing: schedule-time kernel fusion with site affinity

Status: SPEC (2026-07). Target branch: `feat/pointwise-fusion` (off `incoming`).
Implements the fix for the measured GIL dispatch ceiling (24-core run saturates
at ~12 cores with a 10× surplus of ready work; see
`doc/dev/dynamic-scheduler/frontier-scheduler.md`).

## 0. Verdict on the idea

**Semantic queueing — fusing at schedule time, on the ready frontier, instead
of rewriting the plan — is the right design, and strictly better than a static
fusion pass.** Three load-bearing reasons, all verified against the current
code:

1. **The information fusion needs only exists at schedule time.** A node's
   consumers (`graph.consumers`, `graph._dependents`) and goal status are
   built during *registration* (`graph.py:71-90`, `core.py::submit`), not at
   spec production. A static pass runs before that information exists and
   cannot safely classify "interior" (all consumers inside the region) vs
   "exit". At dispatch time the classification is a dict lookup.
2. **No graph rewrite → no interaction with content-addressing.** Node ids,
   `hash_node`, warm-run cache keys, hash-consed dedup, serve/inspect
   addressing: all untouched, because the DAG is never modified. Fusion
   becomes a pure *execution policy* — the scheduler decides to materialize a
   connected set of nodes with one dispatch instead of N. The entire
   "fusion must commute with the cache key" proof obligation of the static
   design disappears.
3. **It fuses across expansion-chunk boundaries for free.** The static pass
   was per-batch (per reduce_chunk); the frontier doesn't care which chunk a
   node came from.

The name is apt and paper-worthy: the ready queue stops holding *syntax*
(individual nodes) and starts holding *semantic execution units* (cones of the
content-addressed store), formed on-line. Static operator fusion (XLA, TVM,
Dask blockwise) is the compile-time special case; this is the schedule-time
generalization, and cache transparency (ids never change) is the novel
property the content-addressed store buys us. Site affinity (§4) turns the
same mechanism into a placement problem (cf. Legion mappers) — the hook for
GPU now and multi-machine later.

**Will it improve throughput? Yes, with high confidence, bounded as follows.**
Today each cheap node pays the full scheduler round trip while holding the
GIL: ready-queue turn, `run_in_executor` submit, future wakeup, `_finish`
bookkeeping — ~0.5–2 ms of GIL-held Python per node, vs ≪0.1 ms of actual
work for `vox1.and` on a typical volume. Inside a fused cone the per-op cost
is one kernel invocation (Stage A, ~50 µs) or amortized into a single numexpr
call (Stage B, ~µs). With the sweep plans' cheap-op fraction (~70% of nodes)
and realistic cone sizes (8–64), GIL-held time per cheap node drops ≥10×.
Expected effect on fmt-5000: plateau lifts from ~12 busy cores toward
SimpleITK-bound (~20+), wall-clock 2–3× on the full-369 sweep. These numbers
are estimates to be *measured and recorded* (§7); do not present them as
results.

## 1. Architecture overview

```
                    ready queue (unchanged)
                          │ pop nid
                          ▼
              ┌───────────────────────────┐
              │ FusionPlanner.plan(nid)   │  grows a cone over the frontier
              │  - elementwise registry   │  using pending/consumers/deps
              │  - claim members (begin)  │  (all O(cone × degree))
              └───────────┬───────────────┘
                          │ Cone (topo-ordered members, inputs, exits)
                          ▼
              ┌───────────────────────────┐
              │ Site placement            │  cpu | gpu (| remote, later)
              └───────────┬───────────────┘
                          │ one dispatch to the site's executor
                          ▼
              ┌───────────────────────────┐
              │ ConeRunner                │  Stage A: loop kernels in-thread
              │                           │  Stage B: numexpr/cupy compiled
              └───────────┬───────────────┘
                          │ {nid: value | SUBSUMED}
                          ▼
              per-member completion in topo order (normal _finish path)
```

Everything above the dispatch is event-loop code (single writer, no locks —
same discipline as today). Everything below is one worker turn.

## 2. Elementwise op registry (prerequisite, tiny)

`PrimitiveSpec` (`primitives/api.py`) gains one optional field:

```python
elementwise: ElementwiseSpec | None = None

@dataclass(frozen=True)
class ElementwiseSpec:
    expr: str            # numexpr fragment, inputs as {0}, {1}, ... placeholders
    out_dtype: str       # dtype of the unfused kernel's output ("uint8", ...)
    commutes_scalar: bool = True   # scalar operands may be inlined as literals
```

Opt in conservatively, in `vox1/__init__.py` next to each kernel entry
(`kernels.py:332-405, 433-478`):

| op | expr | out_dtype | note |
|---|---|---|---|
| `not` | `~({0} != 0)` → cast | uint8 | `sitk.Not`: logical, 0/1 output |
| `and` | `{0} & {1}` | uint8 | `sitk.And` is **bitwise**; identical on 0/1 masks — property-test it |
| `or` | `{0} \| {1}` | uint8 | same caveat |
| `leq_sv` | `{0} <= {1}` → uint8 | uint8 | scalar inlined |
| `geq_sv` | `{0} >= {1}` → uint8 | uint8 | |
| `between` | `({1} <= {0}) & ({0} <= {2})` | uint8 | mirror `sitk.BinaryThreshold` inclusivity exactly |
| `+ * / -` | `{0}+{1}` etc. | float32/input-promoted | mirror sitk type promotion; **exclude any op whose sitk promotion rules you cannot reproduce bit-identically** |

Rule: an op is in the registry only if a property test (§6.1) proves
bit-identical output vs the sitk kernel on randomized inputs including
non-binary values. When in doubt, leave it out — fusion of a subset is still
a win; a silent semantic change is a disaster.

`default.index` / `default.sequence` / anything structural: never elementwise.

## 3. The FusionPlanner (core mechanism)

New module `voxlogica/engine/fusion.py`. Invoked from the worker-turn path in
`core.py` at the point where a popped ready node is about to be dispatched.

### 3.0 When fusion happens (no churn, by construction)

Fusion runs **exactly once per dispatch unit, at pop time** — the last
responsible moment before execution, when a worker has already taken the seed
off the ready queue. It never runs earlier (queued entries are never
inspected, regrouped, or rewritten) and a planned cone is never re-planned:
claims are one-shot and the cone dispatches immediately. There is no
"splitting" operation anywhere in the design, so the fuse/split/fuse
oscillation a queue-resident rebalancer would suffer cannot occur.

The heuristic for *what* to absorb is **ripeness**: a consumer is absorbable
only if all of its other inputs are already complete — i.e. only nodes that
would become ready purely as a consequence of the cone's own completions.
Fusing a ripe chain therefore collapses scheduling steps that were already
guaranteed to happen, in that order, with no new information arriving in
between. A consumer that is not ripe now loses nothing: when its remaining
inputs complete it will be popped normally and seed its own cone. The only
cost of this laziness is potentially smaller cones than a clairvoyant
scheduler could form — observable via `mean_cone_size`, and acceptable.

### 3.1 Cone growth

```
plan(seed) -> Cone | None:
  if spec(seed) not elementwise or fusion disabled: return None  # normal path
  cone = {seed}; frontier = [seed]
  while frontier and len(cone) < CONE_CAP:            # CONE_CAP default 64
    n = frontier.pop()
    for c in graph._dependents[n]:                    # registered consumers only
      if c in cone or not elementwise(c): continue
      if not all(d in cone or d in table.completed for d in graph.deps(c)):
        continue                                      # some input not available
      if not claim(c): continue                       # begin()-style; §3.3
      cone.add(c); frontier.append(c)
  if len(cone) < 2: unclaim; return None              # no benefit, normal path
  classify members:
    interior := all consumers currently in cone AND not a goal
    exit     := everyone else (goals may be exits, never interiors)
  return Cone(topo_order(cone), inputs=external completed deps, exits, interiors)
```

Properties to preserve and assert in tests:

- **Growth is downward only** (from a ready seed into consumers whose other
  inputs are complete). Never absorb producers: a producer of a ready node is
  already complete by definition.
- **O(cone × degree)**, never O(plan): only `_dependents` lists and per-node
  dict lookups. This respects the engine's central invariant.
- **`pending` counts stay consistent**: absorbed members are *not yet ready*
  (`pending[c] ≥ 1`); they are completed by the cone (§3.4), which walks the
  identical `on_complete` path — the counts resolve exactly as if each had
  run individually.
- Shapes: all members must operate on the seed's shape; verify against the
  materialized inputs at plan time (cheap: read `.shape` of PolyArray/first
  image input). Mismatch → cut the cone at that member.

### 3.2 Interaction with dynamic expansion (the subtle case)

The DAG is append-only *during* the run: a later `reduce_chunk` can hash-cons
onto an existing node — i.e. **a new consumer can appear on a node after it
was fused as interior** and its value was never materialized (Stage B).
Sound handling, in order of preference:

1. Registration (`graph.register`) already treats "completed" as available
   and prunes. When the new consumer later executes and the input value is
   missing, the existing `_rematerialize` path runs. Extend it with a
   **recompute fallback**: if the value is neither live nor on disk, and the
   spec's inputs are recoverable, re-execute the node (it is elementwise ⇒
   cheap by definition; recursion bottoms out at persisted/live values).
2. Until the recompute fallback exists, Stage B must not subsume: run
   Stage A semantics (materialize every member's value; interiors get normal
   completion with values, released by refcount as usual). **Ship Stage A
   first for exactly this reason.**

Note this failure mode is *not* introduced by fusion — an evicted,
never-persisted value hit by a late hash-consed consumer has the same hole
today (`persist_min_compute_ms` skips cheap values). The recompute fallback
closes both.

### 3.2b Compile latency (Stage B) is amortized, not on the pop path

Pop-time planning is dict lookups (µs). Stage B's numexpr compilation is the
only real latency, and it keys on the cone's **shape** (canonical expression:
ops, arity, dtypes — not inputs). Sweep plans are shape-repetitive by
construction: one loop body reduced per element means thousands of cones with
the identical canonical expression. So: an **expression cache** (canonical
string → compiled callable, LRU) compiled on first occurrence *on the worker
thread* (never the event loop) and hit thereafter. Expected steady-state
compile cost ≈ one compile per distinct body shape per run. Metric:
`expr_cache_hits/misses`. Optional pre-warm (only if the miss metric ever
matters): loop-body specs exist at expansion time before values are ready, so
canonical cone shapes can be compiled ahead, off-loop, from `reduce_chunk`
output. Stage A compiles nothing — one more reason it ships first.

### 3.3 Claiming

`NodeTable.begin(nid)` already enforces compute-at-most-once. Cone membership
claims each member via the same mechanism before dispatch; a member that
loses the race (already claimed by a normal turn — possible for an exit that
also sits in the ready queue? No: absorbed members have `pending ≥ 1`, so
they are never in the ready queue — assert this) is simply not absorbed.
Keep the assertion as a debug check: *cone members are never queue-resident.*

### 3.4 Completion

The dispatch returns `{nid: value}` (Stage A: every member; Stage B: exits
only, interiors marked SUBSUMED). On the event loop, complete members **in
topological order** through the normal `_finish` flow so that: consumers'
`pending` decrement correctly, input refcounts release, persist policy runs
per node (interiors are cheap → `persist_min_compute_ms` already skips them
today, so Stage B's "no value to persist" is consistent with current policy),
metrics/progress count each node individually (user-visible progress
unchanged). Members completing members: `on_complete(interior)` will "fire"
cone exits; the returned fired list must be filtered against the cone (they
were computed in the same dispatch, do not enqueue them).

`compute_ms` attribution: divide the cone's wall time equally among members
(or by op-count weight). Document that per-node timing inside a cone is
approximate.

### 3.5 Kill switch and knobs

In `config.py`, following house style:

- `VOXLOGICA_FUSION` = `0|1` (default 1) — planner returns None when off.
- `VOXLOGICA_FUSION_CAP` (default 64) — max cone size.
- `VOXLOGICA_FUSION_STAGE` = `a|b` (default `a` until Phase 2 lands).

## 4. Execution sites and affinity

New module `voxlogica/engine/sites.py`.

```python
class Site(Protocol):
    name: str                      # "cpu", "gpu:0", later "remote:host"
    def submit(self, fn, *a) -> Future
    def available(self) -> bool

class CpuSite:   # wraps the existing ThreadPoolExecutor — today's behavior
class GpuSite:   # one dedicated thread + CUDA stream; exists iff cupy imports
```

- **Placement** happens per dispatch unit (single node or cone), by a
  heuristic that is deliberately simple and logged:
  `gpu if gpu.available() and cone.ops × voxels ≥ GPU_MIN_WORK and
  (inputs already device-resident or transfer_bytes / est_bandwidth < saved_time)`.
  Everything else → cpu. Heuristic lives in one function with its constants
  as module-level named values; no scattered conditionals.
- **Residency** is a property of the value, not the scheduler: the PolyArray
  (§5) records which sites hold a buffer. Transfers are explicit
  (`poly.to(site)`), counted in metrics (`gpu_transfers`, `gpu_bytes_moved`),
  and visible in the memlog.
- **GPU backend now**: CuPy elementwise evaluation of Stage B cones (the
  expression tree maps 1:1 to cupy ufuncs; or `cupy.fuse` for the whole
  cone). Optional dependency; `GpuSite.available()` is False on machines
  without CUDA (all Macs) and every code path degrades to cpu. CI must pass
  with and without cupy installed.
- **Multi-machine (future, design constraint only)**: a `RemoteSite` submits
  (cone spec + input node ids) and the content-addressed store is the
  transfer fabric — inputs are fetched by id, results written back by id.
  Nothing in the Site protocol may assume shared memory. This is a paper
  section, not a Phase 1–3 deliverable.
- Oversubscription control: numexpr `set_num_threads(1)`; one GPU launch
  thread per device. Outer parallelism belongs to the scheduler alone.

## 5. PolyArray: one value, many views

New module `voxlogica/arrays.py`. The single value type flowing between
kernels for volumetric data (scalars/sequences unaffected).

```python
@dataclass
class Geometry:                    # sitk metadata, hashable
    spacing: tuple[float, ...]
    origin: tuple[float, ...]
    direction: tuple[float, ...]

class PolyArray:
    geometry: Geometry
    dtype, shape                   # canonical (numpy) description
    _views: dict[str, Any]         # "np" | "sitk" | "cupy" — cached, lazily built
    # constructors
    @classmethod from_sitk(img)    # zero-copy: GetArrayViewFromImage + geometry
    @classmethod from_numpy(arr, geometry)
    # views
    def np(self)  -> np.ndarray    # zero-copy when host-resident
    def sitk(self) -> sitk.Image   # built on first request (one copy — sitk
                                   #   cannot wrap foreign buffers), then cached
    def cupy(self) -> cp.ndarray   # device view; triggers transfer if absent
    def __dlpack__(self)           # torch/tf/jax interop for free
    def to(self, site) / nbytes / release_site(site)
```

Honest constraints, stated in the module docstring:

- sitk→numpy is zero-copy **read-only** (`GetArrayViewFromImage`); fused
  kernels must never write through it — outputs go to fresh/pooled buffers.
- numpy→sitk **copies** (SimpleITK owns its buffers). The design minimizes
  crossings, it cannot eliminate them: a chain of fused cones stays in
  numpy/cupy end-to-end; the sitk view is built only when a legacy sitk
  kernel actually consumes the value, then cached so it's paid once.
- `nbytes` counts all resident views (host + device) — `node_table`
  accounting and the memlog must see the true footprint.

**Kernel-boundary compatibility (zero kernel rewrites):** the executor's
`_compute` (`executor.py:36-48`) unwraps `PolyArray → .sitk()` for kernels
that expect images, and wraps `sitk.Image` returns into
`PolyArray.from_sitk`. Existing kernels remain untouched. Detection: values
are wrapped at completion time; unwrapping keys off isinstance. Keep the
adapter in one place (executor), not scattered.

**Buffer pool** (second-order; implement last): per `(site, shape, dtype)`
free-list with a byte cap. `table.evict`/last-release returns poolable
(fusion-produced) buffers; Stage B writes through `out=` into pooled buffers.
Metrics: `pool_hits`, `pool_bytes`. Skip pooling sitk-owned buffers.

## 6. Testing (gates, per phase)

1. **Elementwise equivalence (property tests, the critical gate):** for each
   registry op and for random cones (random shapes incl. 3D, random dtypes
   incl. non-binary uint8 values, NaNs for float ops): fused output is
   **bit-identical** (`np.array_equal` + dtype equality) to the unfused
   sitk-kernel chain, in both Stage A and Stage B, cpu and (if present) gpu.
2. **Planner invariants:** absorbed members never queue-resident; interiors
   never goals; cone growth cut at shape mismatch / non-elementwise /
   unavailable dep; determinism of results (not of cone shapes — cones are
   timing-dependent by design; results must not be).
3. **Scheduler bookkeeping:** after a fused run, `pending`/`consumers`/
   `incomplete` are empty exactly as after an unfused run; progress totals
   identical; refcount releases identical (assert via liveness counters).
4. **Warm-run:** fused cold run then warm run reuses (kernels ≈ 0); warm run
   with `VOXLOGICA_FUSION=0` after fused cold run is correct.
5. **Late-consumer recompute** (Phase 2 gate): construct a run where a second
   loop chunk hash-conses onto a subsumed interior; assert recompute path
   produces the right value.
6. **Full regression:** entire existing suite green with fusion default-on;
   one real `.imgql` compared fusion-on vs fusion-off (identical printed
   output).
7. **PolyArray:** zero-copy assertions (`np.shares_memory`), geometry
   round-trip sitk→poly→sitk, nbytes accounting, dlpack round-trip with
   torch if importable.

## 7. Perf evidence (record, don't assert)

`tests/perf/bench_scheduler.py` cheap-kernel config
(`--elements 150 --width 1000 --rounds 4`) and, if accessible, the fmt-5000
full-369 sweep. Record before/after: nodes/s, `mean_busy_cores`, peak RSS,
new counters (`cones_dispatched`, `ops_fused`, `mean_cone_size`,
`gpu_transfers`). Write results into
`doc/dev/dynamic-scheduler/frontier-scheduler.md` §"Semantic queueing" with
the same honesty rules as the existing sections.

## 8. Phases (each = one committable, benchmarked unit on `feat/pointwise-fusion`)

| phase | deliverable | risk | gate |
|---|---|---|---|
| **0** | `PolyArray` + executor boundary adapters + accounting. No behavior change. | low | §6.7, full regression |
| **1** | ElementwiseSpec registry + FusionPlanner + **Stage A** cone dispatch (loop existing kernels inside one worker turn, all values materialized). Kill switch, metrics. | medium | §6.1–4, §6.6, bench |
| **2** | **Stage B**: numexpr-compiled cones, interior subsumption, recompute fallback in `_rematerialize`. | medium-high | §6.1, §6.5, bench |
| **3** | Sites + affinity + CuPy GPU backend + residency/transfer metrics. | medium | §6.1 on gpu, CI without cupy |
| **4** | Buffer pool. Then doc + paper notes ("semantic queueing", affinity). | low | pool metrics, no leak under valgrind-style stress test |

Phase 1 alone attacks the measured bottleneck (dispatch tax) and is
independently shippable. Do not start Phase 2 before Phase 1's bench numbers
are recorded. Implementation style: follow the engine's existing conventions
— module docstrings stating invariants, O(frontier) discipline, no dead code,
no speculative generality beyond the Site protocol (which is the deliberate
extension point).
