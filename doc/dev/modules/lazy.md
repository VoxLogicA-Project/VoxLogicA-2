# lazy/ - Symbolic Planning IR

## Canonical Code
- `implementation/python/voxlogica/lazy/ir.py`
- `implementation/python/voxlogica/lazy/hash.py`
- `implementation/python/voxlogica/lazy/plan.py`

## Purpose
`lazy/` defines the symbolic graph emitted by the reducer. It does not execute primitives or perform I/O.

## Main Types
- `NodeSpec`: symbolic node (`constant`, `primitive`, `closure`)
- `GoalSpec`: terminal side-effect goal (`print`, `save`)
- `SymbolicPlan`: immutable graph + goals + imported namespaces
- `Ref`: typed node reference wrapper

## Guarantees
1. Deterministic node hashing (`hash_node`) with canonical serialization.
2. Symbolic-only data; no materialized runtime results in this layer.
3. Stable IDs for identical node payloads.
