# Primitives API (Authoritative)

## Status
- Version: 2026-02-24
- Scope: `implementation/python/voxlogica/primitives/api.py`, `implementation/python/voxlogica/primitives/registry.py`

## Core Contract
A primitive is declared by `PrimitiveSpec`.

Required fields:
- `name: str`
- `kind: Literal["scalar", "sequence", "tree", "dataset", "effect"]`
- `arity: AritySpec`
- `attrs_schema: dict[str, AttrType]`
- `planner: Callable[[PrimitiveCall], NodeSpec]`
- `kernel_name: str`

`PrimitiveCall` is symbolic-only:
- `args: tuple[NodeId, ...]` (symbolic refs only)
- `kwargs: tuple[(str, NodeId), ...]` (symbolic refs only)
- `attrs: dict[str, Any]` (literal configuration only)

## Runtime Kernel Contract
- Kernel dispatch is by `kernel_name`.
- New-style kernels must not depend on runtime internals (`engine`, `storage`, `session` parameters are rejected).
- Kernel inputs are resolved runtime values for referenced nodes and literal attrs.

## Planner Contract
- Planner receives a `PrimitiveCall` and returns a `NodeSpec`.
- Planner must not perform I/O or execution.
- Planner must be deterministic for equal symbolic inputs.

## Registry Contract
`PrimitiveRegistry` provides:
- Deterministic namespace discovery and module loading.
- Validation on registration.
- Deterministic qualified lookup (`namespace.primitive`).
- Deterministic unqualified lookup using explicit import order (`default` first, then imported namespaces).

No set-order behavior is allowed in primitive resolution.

## Migration Rules
Legacy primitive modules (`execute(**kwargs)` / `register_primitives()`) are supported via adapter with deprecation warning.

Migration target:
1. Define `PRIMITIVE_SPEC`.
2. Expose explicit `KERNEL` (or `execute` alias).
3. Remove access to runtime internals from primitive code.
4. Keep behavior stable and add contract tests.

## Examples
`load(dataset)`:
- Planner emits a dataset node.
- Runtime kernel performs load at execution/materialization time only.

`map(f, seq)`:
- Planner emits a sequence node referencing symbolic `f` and `seq`.
- Runtime decides strict, paginated, or streamed materialization.

`for_loop`:
- Planner lowers to symbolic map/loop node.
- Runtime strategy defines when/where iterations are computed.

`save(path, value)` and `print(label, value)`:
- Planner emits effect nodes/goals.
- Runtime materializes side effects only at goal execution.
