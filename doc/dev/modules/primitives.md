# primitives/ - Primitive Libraries and Registration

## Canonical References
- Contract: `doc/dev/primitives-api.md`
- Code: `implementation/python/voxlogica/primitives/api.py`
- Code: `implementation/python/voxlogica/primitives/registry.py`

## Purpose
`primitives/` hosts callable kernels grouped by namespace (for example `default`, `simpleitk`, `arrays`, `nnunet`).

The runtime does not call primitive modules directly. It resolves a `PrimitiveSpec` through `PrimitiveRegistry`, then dispatches by `kernel_name`.
Namespaces may also export ImgQL libraries (`*.imgql`) colocated in the namespace directory; these declarations are imported when the namespace is imported.

## Current Resolution Rules
1. Qualified call (`namespace.primitive`) is exact.
2. Unqualified call resolves deterministically: `default` first, then explicitly imported namespaces, then remaining namespaces in lexical order.
3. In-repo primitive modules are fully migrated to stable `PrimitiveSpec` contracts (legacy adapter remains only as external compatibility path).

## Module Authoring Rules
1. New primitives should expose `PRIMITIVE_SPEC` and `KERNEL` (or `execute` alias).
2. Planner behavior is symbolic-only; no execution, no I/O.
3. Kernel behavior is runtime-only; no dependency on engine/storage internals.
4. Add tests under `tests/contract/` for new/changed primitives.
5. If operators are better expressed in ImgQL, place them in namespace-local `*.imgql` files. They are loaded and exported together with Python primitives.

## Interim Non-Legacy Blocklist

Until the full manual audit is completed, the runtime applies a conservative policy in non-legacy mode:

1. Block all primitives declared with `kind="effect"`.
2. Block additional SimpleITK calls by name prefix even if currently typed as scalar:
- `Write*`
- `ImageViewer_SetGlobalDefault*`
- `ProcessObject_SetGlobal*`

Serve mode additionally applies read-root restrictions for `ReadImage`, `ReadTransform`, and `load(path)` using:
- `VOXLOGICA_SERVE_DATA_DIR`
- `VOXLOGICA_SERVE_EXTRA_READ_ROOTS`

## Manual Audit Process

- A dedicated GitHub issue tracks manual auditing of all exported primitives across namespaces: `#13`.
- Audit output must classify each primitive (`pure`, `read-side-effect`, `write-side-effect`, `global-mutable`, `external-process`) and provide source-backed rationale.
- Final audited policy matrix will replace this interim conservative blocklist.
