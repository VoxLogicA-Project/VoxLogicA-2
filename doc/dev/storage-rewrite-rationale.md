# Storage Rewrite Rationale

## Why `storage.py` Became Complex

The previous storage implementation mixed multiple responsibilities in one file:

1. Result persistence (`results` table and serialization)
2. Execution state tracking (`execution_state`, `session_state`, stale cleanup)
3. Background write queues and lifecycle hooks
4. Legacy coordination concerns (operation status/futures-era behavior)
5. New runtime split stores (`DefinitionStore`, `MaterializationStore`)

This produced architectural problems:

1. **Conflated responsibilities**  
   Definition-time metadata, runtime materialization, and persistence policy were coupled.

2. **Hidden control flow**  
   Background threads, queue-based persistence, and implicit cleanup made behavior hard to reason about and test.

3. **Legacy/runtime split mismatch**  
   The new symbolic planner + pluggable executors architecture only needs:
   - deterministic definition store
   - materialization store
   - optional durable results persistence
   but inherited broader state-machine logic from the previous execution model.

4. **Low modularity for backend replacement**  
   Storage concerns were not exposed through a clear backend contract, making replacement or strategy-specific behavior expensive.

## Rewrite Principles

The rewrite enforces:

1. **Single responsibility layers**
   - `ResultsDatabase` for persistence only
   - `DefinitionStore` for immutable symbolic definitions
   - `MaterializationStore` for runtime artifacts and failures

2. **Stable open API**
   - backend interface with clear methods (`put_success`, `put_failure`, `get_record`, `has`, `delete`, `clear`, `close`)
   - multiple implementations (`SQLiteResultsDatabase`, `InMemoryResultsDatabase`)

3. **Explicit policy knobs**
   - read-through and write-through behavior in `MaterializationStore`
   - no hidden background writers

4. **Compatibility shims with clear boundaries**
   - `StorageBackend`/`NoCacheStorageBackend` kept as compatibility aliases
   - legacy execution-state machinery removed from runtime-critical path

## Acceptance Criteria for Rewrite

1. Execution strategies compile/run using `DefinitionStore` + `MaterializationStore`.
2. Durable result persistence is optional and backend-driven.
3. No reducer-time storage writes.
4. API is small, typed, documented, and backend-replaceable.
