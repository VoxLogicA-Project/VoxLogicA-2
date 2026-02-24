# Lazy Planner + Execution Strategy Split

## Objective
Split runtime into two strict layers:
1. Symbolic planning (`lazy/*`, reducer output only).
2. Pluggable execution strategies (`execution_strategy/*`) that decide evaluation semantics.

This preserves DSL behavior while making execution replaceable (Dask-first, strict fallback).

## Architecture Diagram

```text
Parser -> Reducer -> SymbolicPlan
                    |
                    v
          RuntimeFacade (execution.py)
             |                |
             v                v
      DaskStrategy      StrictStrategy
             \             /
              \           /
             MaterializationStore

DefinitionStore (NodeId -> NodeSpec) is immutable after reduction.
```

## Module Boundaries
- `voxlogica.reducer`
  - Emits symbolic `NodeSpec` + `GoalSpec` only.
  - No I/O, no storage writes, no execution side effects.
- `voxlogica.lazy`
  - Canonical symbolic IR and node hashing.
- `voxlogica.execution_strategy`
  - Compiles and executes symbolic plans.
  - Exposes run/stream/page semantics.
- `voxlogica.storage`
  - Definition store: immutable symbolic graph.
  - Materialization store: runtime artifacts and metadata.
- `voxlogica.features`
  - Stable facade; orchestrates parser/reducer/runtime selection and output payloads.

## Migration Phases
1. Stabilize primitive API and deterministic registry.
2. Introduce symbolic IR and hashing.
3. Refactor reducer to pure symbolic output.
4. Add strategy interfaces + strict strategy.
5. Add Dask strategy (default).
6. Rewire runtime facade and `features.py` internals.
7. Replace test harness with pytest layered suites and CI gates.

## Compatibility Rules
- Stable externally:
  - DSL syntax
  - CLI/API commands (`run`, `serve`, `version`, `list-primitives`)
  - `features.py` result schema
- Deprecated during migration:
  - Legacy `execute(**kwargs)` primitive modules (adapter-backed, warning emitted)

## Acceptance Criteria
1. Reducer emits only symbolic nodes/goals.
2. `load(dataset)` reduces without I/O.
3. `map`/`for` reduce to symbolic sequence nodes.
4. Same symbolic plan executes on strict and dask with equivalent logical results.
5. Streaming and pagination work without mandatory full materialization.
6. Primitive lookup is deterministic for qualified/unqualified names.
7. CI enforces lint/type/unit/contract/integration gates.

## Risk Controls
- Add contract tests before broad migration of primitive modules.
- Keep `features.py` as facade to avoid user-facing churn.
- Introduce fallback strict strategy before defaulting to Dask.
- Keep adapter window for legacy primitives, with explicit warnings.
- Land changes as small vertical commits tied to one tracking issue.
