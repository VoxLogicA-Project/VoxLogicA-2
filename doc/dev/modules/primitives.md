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
