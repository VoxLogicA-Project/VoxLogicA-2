# execution.py + execution_strategy/ - Runtime Facade and Strategies

## Canonical Code
- Facade: `implementation/python/voxlogica/execution.py`
- Strategies: `implementation/python/voxlogica/execution_strategy/*`

## Architecture
- `execution.py` is a thin runtime facade.
- Strategy implementations own execution semantics:
  - `DaskExecutionStrategy` (default)
  - `StrictExecutionStrategy` (fallback)

## Strategy Contract
`ExecutionStrategy` defines:
1. `compile(plan) -> PreparedPlan`
2. `run(prepared, goals) -> ExecutionResult`
3. `stream(prepared, node, chunk_size)`
4. `page(prepared, node, offset, limit)`

## Storage Alignment
Prepared execution uses:
- `DefinitionStore`: immutable symbolic definitions (`NodeId -> NodeSpec`)
- `MaterializationStore`: runtime value/artifact records (`NodeId -> result metadata`)

No reducer-time materialization writes are allowed.
