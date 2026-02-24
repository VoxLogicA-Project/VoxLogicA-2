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

## Kept for Compilation, Needs Rewrite

These modules remain in tree to keep the app functional, but should be rewritten for long-term architecture quality:

1. `implementation/python/voxlogica/features.py`  
   Reason: monolithic orchestration (CLI/API + execution + export handling) with compatibility branching.

2. `implementation/python/voxlogica/main.py`  
   Reason: CLI/API command wiring and runtime flags are tightly coupled in one file.

3. `implementation/python/voxlogica/primitives/simpleitk/__init__.py`  
   Reason: large dynamic introspection wrapper; should be split into generated spec/index + curated kernels.

4. `implementation/python/voxlogica/primitives/arrays/__init__.py`  
   Reason: many unrelated kernels in one file; should be split into per-primitive modules.

5. `implementation/python/voxlogica/primitives/nnunet/__init__.py`  
   Reason: large operational workflow module with external process orchestration and mixed concerns.

6. `implementation/python/voxlogica/primitives/test/*`  
   Reason: experimental/demo behavior mixed with runtime-visible primitives; should be isolated as dedicated fixture/test plugins.

7. `implementation/python/voxlogica/converters/{json_converter.py,dot_converter.py}`  
   Reason: compatibility-heavy serializers; should be normalized around `SymbolicPlan` as sole internal format.

## Notes

This frontier intentionally preserves user-facing behavior while reducing architectural debt in active runtime modules.  
Further deletions or rewrites should proceed in issue-scoped slices to avoid broad behavioral churn.
