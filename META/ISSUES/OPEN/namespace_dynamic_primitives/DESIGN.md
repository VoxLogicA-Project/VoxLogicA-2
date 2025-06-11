# Namespace-based and Dynamic Primitive Loading: Design and Requirements

## Background
VoxLogicA-2 currently implements each primitive as a separate Python file in `implementation/python/voxlogica/primitives/`, with a dynamic loader that imports modules and looks for an `execute(**kwargs)` function. This is not scalable for large external libraries (e.g., SimpleITK) and does not support grouping or namespacing of primitives.

## Requirements
- **Namespace Abstraction:**
  - Primitives can be grouped into namespaces, represented as subdirectories under `primitives/` (e.g., `primitives/simpleitk/`).
  - Each namespace can provide primitives either as static files or via dynamic registration.
- **Dynamic Namespace Support:**
  - A namespace (e.g., `simpleitk`) can register primitives at runtime by introspecting a library and exposing its functions as primitives.
  - No need to write a file per primitive for dynamic namespaces.
- **Loader Refactoring:**
  - The primitive loader must support namespace-aware discovery and loading.
  - Operator names may be qualified (e.g., `simpleitk.GaussianBlur`).
- **Import Command Extension:**
  - The `import` command in ImgQL can now import primitive namespaces and submodules, not just files. For example, `import "default"` or `import "simpleitk"` is permitted and will make the corresponding namespace's primitives available.
- **Backward Compatibility:**
  - Existing static primitives and operator loading must continue to work.
- **Documentation:**
  - Update developer documentation to describe the new namespace and dynamic loading system, and the extended import command.

## Design Ideas
- **Namespace Directory Structure:**
  - `primitives/` contains static primitives as before.
  - Subdirectories (e.g., `primitives/simpleitk/`) represent namespaces.
  - A namespace may contain an `__init__.py` that registers dynamic primitives.
- **Dynamic Registration API:**
  - A namespace can provide a `register_primitives()` function that returns a mapping from operator names to callables.
  - The loader, when encountering a namespace, calls this function to register all primitives in that namespace.
- **Operator Name Resolution:**
  - Operator names may be qualified (e.g., `simpleitk.GaussianBlur`).
  - The loader resolves operator names to the correct namespace and primitive.
- **Import Command Semantics:**
  - The `import` command is extended to support importing primitive namespaces and submodules. When a namespace is imported, all its primitives become available in the current scope. This includes static, dynamic, and default namespaces.
- **SimpleITK Example:**
  - `primitives/simpleitk/__init__.py` implements `register_primitives()` by introspecting the SimpleITK module and exposing all suitable functions as primitives.
  - Each function is wrapped to conform to the `execute(**kwargs)` interface.

## Implementation Sketch
1. **Refactor Loader:**
   - Update `PrimitivesLoader` to support namespace-based lookup and dynamic registration.
   - On initialization, scan subdirectories for namespaces.
   - For each namespace, check for `register_primitives()` and call it if present.
2. **Operator Name Handling:**
   - Support both unqualified (legacy) and qualified operator names.
   - For qualified names, resolve to the correct namespace and primitive.
3. **Dynamic Namespace Example:**
   - In `primitives/simpleitk/__init__.py`, implement `register_primitives()`:
     - Use `dir(SimpleITK)` and `inspect` to enumerate functions.
     - For each function, create a wrapper that adapts the signature to `execute(**kwargs)`.
     - Return a mapping `{ 'GaussianBlur': wrapper, ... }`.
4. **Documentation:**
   - Update `doc/dev/implementing-new-operators.md` and related docs to describe namespaces and dynamic loading.

## Open Questions
- How to handle name collisions between static and dynamic primitives?
- Should dynamic namespaces support selective exposure (e.g., only a whitelist of functions)?
- How to document dynamically registered primitives for end users?

## References
- See AGENT.md for project policies.
- See current implementation in `voxlogica/primitives/` and `voxlogica/execution.py`.
