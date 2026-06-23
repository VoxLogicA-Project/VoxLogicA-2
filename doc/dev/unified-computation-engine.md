# Unified Computation Engine — design

Status: implemented and working on `feat/unified-execution`, opt-in via
`VOXLOGICA_ENGINE=1`. The proven `lazy` strategy remains the default until the
engine has had broad real-workload validation.

Implemented (`voxlogica/engine/`): `NodeTable` (Merkle identity, tiered values,
the enforced no-double-computation guard), `ComputationEngine` (priority
scheduler, submit/await/prioritise), pure `Executor`, single-semantics
`Expander`, `Query` handles, memory-pressure eviction, and an
`EngineExecutionStrategy` adapter.

Key correctness property: readiness is gated on **completion** (monotonic), never
on value residency. Values are evicted under a byte budget and **rematerialised on
demand**, so eviction can only ever cost a recompute — never a deadlock. This is
what made multi-query sharing and aggressive eviction safe.

Validated: single- and multi-query evaluation, nested runtime loops, the real-data
oracle (matches `lazy` exactly), 18 unit tests under the engine, and correctness
under a forced 1 MB / 300 MB live-tier budget (real image eviction + rematerialise).
`VOXLOGICA_ENGINE_DEBUG=1` dumps the stuck frontier if a future change regresses.

## 1. Vision — a computation base, not a database

VoxLogicA-2 becomes a **live computation base**: a persistent process holding a
content-addressed (Merkle) DAG of *expressions* and their memoized *results*
across cache tiers. You do not store data and query it; you store **computations
(recipes)** and **ask for their values**. Identical sub-recipes are computed once,
ever, and shared across every query that needs them.

A session is interactive and live:

1. The user submits an ImgQL line (a **query**).
2. It is reduced into DAG nodes, deduplicated against everything already known.
3. Evaluation starts immediately, in parallel; progress is reported live.
4. A later query can be **prioritized above** in-flight work — its tasks are
   queued just above the older ones, transparently sharing all common
   dependencies — or left at normal priority. The user chooses.

## 2. One semantics

Today two evaluators coexist:

- the **reducer** (`reduce_expression`): AST → DAG nodes; and
- the **runtime AST interpreter** (`_evaluate_runtime_expression`, with
  `RuntimeClosure`/`RuntimeFunction`): AST → values directly, invoked by the
  `for_loop`/`map`/`filter`/`fold` kernels to evaluate closure bodies.

The redesign keeps **exactly one**: reduction (AST → nodes). Loop/map/filter
bodies are never interpreted to values. When an iterable's value is known, the
body is *reduced* per element into nodes — exactly what dynamic expansion (#22)
already does. Kernels only ever compute a single primitive over
already-materialized inputs.

Consequences:

- `_evaluate_runtime_expression`, `RuntimeClosure`, `RuntimeFunction`, and the
  legacy recursive `_evaluate_node_lazy` are removed.
- `fold` becomes a reduction node over an expanded sequence; `filter` becomes
  predicate nodes + a gather. The language's meaning lives in one place.

The expander is an **abstract interpretation**: it computes the DAG (structure)
as far as data allows, *without* computing values — unrolling everything whose
shape is fixed by known data (constants now; runtime iterables the instant their
value lands), and leaving genuinely data-dependent structure for later.

## 3. Components

All coordination runs on a single asyncio event loop (single-writer over shared
maps, no locks); ITK kernels run on a thread pool (GIL released → real
parallelism). The store's persistence already has its own thread.

1. **Reducer / Expander.** Incremental. Turns each query into nodes; hash-conses
   against the global node table; streams ready nodes to the scheduler as it
   produces them. Re-entrant for runtime expansion.
2. **Scheduler.** Priority task graph: `pending` (unsatisfied deps), `dependents`
   (reverse edges), `consumers` (for eviction). Zero-in-degree nodes are
   runnable. Workers pull **highest-priority ready** first.
3. **Executor.** Thread pool; pure `inputs → value` per primitive.
4. **Evictor / tiered store.** When a value's last *current* consumer across all
   live queries has run, demote RAM (tier-1) → disk/SQLite (tier-2); reload on
   demand. Merkle keys make demotion safe and shareable. (Two-level cache from
   #18 is the substrate.)
5. **Live reporter.** Per-query and global progress; query state.

## 4. Priorities

Priority is **per-demand, aggregated per-node**: a node's effective priority is
the max over all queries currently demanding it. Submitting a high-priority
query raises the priority of its (possibly already-queued) transitive
dependencies — "queue just above" is a priority bump propagated along
dependency edges. The ready queue is a priority queue keyed on effective
priority (ties broken by depth for memory locality, replacing today's LIFO).

Lowering/cancelling a query removes its contribution; a node with no remaining
demanders is dropped from the ready set (if not yet running) and its scheduled
descendants are pruned unless demanded elsewhere.

## 5. Invariants

- **Single identity**: Merkle hash over (operator, child-ids, attrs). Dedup,
  cache, and cross-query sharing all key on it.
- **Idempotent demand**: asking for a known node awaits/returns its value; never
  recomputed concurrently (in-flight nodes are awaited, not duplicated).
- **Monotonic DAG**: nodes are only added; values may be evicted (they are
  recomputable from the recipe) but identities are stable.
- **No double computation (enforced)**: a node id is dispatched to the executor
  at most once while unmaterialized. If the engine is ever about to start a
  second computation for a hash that is already running or already materialized,
  it **raises** rather than proceeding — the whole point of content addressing is
  that this never happens, so a violation is a scheduler bug we want to catch
  loudly, not absorb.

## 6. Public surface

```
engine = ComputationEngine(store=...)
h = engine.submit(imgql_line, priority=NORMAL)   # -> QueryHandle
h.status      # pending | running | done | failed | cancelled
await h.result()
engine.prioritize(h, HIGH)                         # bump h and its deps
h.cancel()
```

The existing CLI `voxlogica run file.imgql` becomes: submit every goal at normal
priority and await all — a thin client of the engine.

## 7. Staged roadmap

Each stage is a branch → PR → `incoming`, independently shippable and tested.

- **M1 — One semantics.** for_loop/map/filter/fold always expand via the
  reducer; delete the runtime AST interpreter and legacy DFS. Behavior
  identical; large simplification. Builds directly on #22.
- **M2 — Engine core.** Extract a persistent `ComputationEngine` (node table,
  scheduler, executor, tiered store) from the one-shot `run()`. CLI drives it for
  a single batch.
- **M3 — Priorities.** Priority-ready queue; per-node aggregated priority;
  `prioritize()` propagation; cancellation.
- **M4 — Live queries.** `submit()` while running; incremental reduction into the
  live table; dedup against in-flight nodes; per-query progress.
- **M5 — Front-end.** REPL and/or HTTP API; live progress hook for the UI.

## 8. Risks

- The study runs on this engine: each stage must preserve `voxlogica run`
  semantics and pass the regression suite before merge.
- Eviction × cross-query sharing × runtime expansion is the subtle part
  (see #22 fixes: ready-gating, capture pinning, rematerialization of evicted
  loop-invariants). Priorities add re-ordering on top; property tests over random
  DAGs are warranted.
