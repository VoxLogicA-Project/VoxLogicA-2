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
- Unknown callable names are a static error during reduction. Reducer must fail deterministically (no unresolved-call fallback nodes).

## Registry Contract
`PrimitiveRegistry` provides:
- Deterministic namespace discovery and module loading.
- Validation on registration.
- Deterministic qualified lookup (`namespace.primitive`).
- Deterministic unqualified lookup using explicit import order (`default` first, then imported namespaces).
- Namespace-level ImgQL library exports (`*.imgql`) loaded from each primitive namespace and injected on namespace import.
- Optional namespace runtime reset hook (`reset_runtime_state()`) executed at strategy run start.

No set-order behavior is allowed in primitive resolution.

## Static Policy Contract

- Default runtime mode is non-legacy.
- Non-legacy blocks side effects by static policy:
  - all primitives with `kind="effect"`
  - conservative SimpleITK mutable/global APIs:
    - `Write*`
    - `ImageViewer_SetGlobalDefault*`
    - `ProcessObject_SetGlobal*`
- Serve mode additionally enforces read-root policy for `ReadImage`, `ReadTransform`, and `load(path)`:
  - allowed roots from `VOXLOGICA_SERVE_DATA_DIR` plus `VOXLOGICA_SERVE_EXTRA_READ_ROOTS`
  - out-of-root reads are rejected with deterministic diagnostics/runtime errors.

## Migration Rules
Legacy primitive modules (`execute(**kwargs)` / `register_primitives()`) are supported via adapter with deprecation warning.

Repository status:
- All in-repo primitive namespaces are migrated to `PrimitiveSpec`.
- Legacy adapter path is retained for external/third-party compatibility only.

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
- Runtime decides dask materialization timing and paginated inspection behavior.

`for_loop`:
- Planner lowers to symbolic map/loop node.
- Runtime execution defines when/where iterations are computed.

`save(path, value)` and `print(label, value)`:
- Planner emits effect nodes/goals.
- Runtime materializes side effects only at goal execution.
