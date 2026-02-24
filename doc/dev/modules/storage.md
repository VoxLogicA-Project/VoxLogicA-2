# storage.py - Definition and Materialization Stores

## Canonical Code
- `implementation/python/voxlogica/storage.py`

## Runtime Split
`storage.py` now exposes two runtime store roles for the execution split:

1. `DefinitionStore`
- Holds symbolic definitions only.
- Mapping: `NodeId -> NodeSpec`.
- Immutable snapshot semantics for a compiled plan.

2. `MaterializationStore`
- Holds runtime artifacts only.
- Mapping: `NodeId -> MaterializationRecord(status, value, metadata)`.
- Strategy-owned lifecycle (`materialized` / `failed`).

## Legacy Backend
`StorageBackend` (SQLite/WAL) remains available for backward compatibility and caching paths outside the new strategy flow.

## Contract Rule
Reducer and planner must never write materialized results. Materialization happens only inside execution strategies.
