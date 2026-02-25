# Codebase Rewrite Frontier (2026-02-25)

## Scope Reviewed

Reviewed active runtime modules under:
- `implementation/python/voxlogica/`
- `tests/`
- runtime docs in `doc/dev/`

Removed stale files that are no longer part of the current architecture:
- entire `tests/archive/legacy/` tree (superseded script-era tests and old fixtures)

## Stable Frontier (keep as baseline)

These modules are treated as stable core for the current architecture:

1. `implementation/python/voxlogica/lazy/*`
2. `implementation/python/voxlogica/reducer.py`
3. `implementation/python/voxlogica/primitives/api.py`
4. `implementation/python/voxlogica/primitives/registry.py`
5. `implementation/python/voxlogica/execution_strategy/{base.py,results.py,strict.py,dask.py}`
6. `implementation/python/voxlogica/storage.py` (rewritten modular API)
7. `implementation/python/voxlogica/execution.py` (strategy facade)
8. `implementation/python/voxlogica/repl.py` (interactive session runtime)

## Rewrite Status

Previously flagged rewrite targets were completed:

1. `implementation/python/voxlogica/features.py` rewritten to modular handler pipeline.
2. `implementation/python/voxlogica/main.py` rewritten with clean CLI/API composition and REPL command.
3. `implementation/python/voxlogica/primitives/simpleitk` split into namespace facade + runtime module.
4. `implementation/python/voxlogica/primitives/arrays` split into namespace facade + kernels module.
5. `implementation/python/voxlogica/primitives/nnunet` split into namespace facade + kernels module.
6. `implementation/python/voxlogica/converters` normalized with shared converter helpers.

No mandatory rewrite blockers remain for the current runtime architecture.

## Notes

This frontier intentionally preserves user-facing behavior while reducing architectural debt in active runtime modules.  
Further deletions or rewrites should proceed in issue-scoped slices to avoid broad behavioral churn.
