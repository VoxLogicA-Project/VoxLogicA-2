# storage.py - Definition, Materialization, and Results DB

## Canonical Code
- `implementation/python/voxlogica/storage.py`
- Rewrite rationale: `doc/dev/storage-rewrite-rationale.md`

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
- Optional read/write-through integration with `ResultsDatabase`.

## Results Database API
`ResultsDatabase` is the stable backend contract:
- `has(node_id)`
- `get_record(node_id)`
- `put_success(node_id, value, metadata)`
- `put_failure(node_id, error, metadata)`
- `delete(node_id)`
- `clear()`
- `close()`

Built-in backends:
- `SQLiteResultsDatabase` (durable)
- `InMemoryResultsDatabase` (ephemeral)
- `StorageBackend` and `NoCacheStorageBackend` compatibility aliases

## Contract Rule
Reducer and planner must never write materialized results. Materialization happens only inside execution strategies.
