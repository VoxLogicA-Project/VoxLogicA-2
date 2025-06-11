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
  - Qualified imports can also apply to files for more precise control.
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
  - The `default` namespace is automatically available without explicit import for backward compatibility.
  - Qualified imports can also apply to files for more precise control.
- **SimpleITK Example:**
  - `primitives/simpleitk/__init__.py` implements `register_primitives()` by introspecting the SimpleITK module and exposing all suitable functions as primitives.
  - Each function is wrapped to conform to the primitive interface, handling argument mapping from VoxLogicA's internal format to SimpleITK's expected parameters.

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
     - For each function, create a wrapper that adapts VoxLogicA's argument format to SimpleITK's expected parameters.
     - Return a mapping `{ 'GaussianBlur': wrapper, ... }`.
4. **Documentation:**
   - Update `doc/dev/implementing-new-operators.md` and related docs to describe namespaces and dynamic loading.

## Namespace Collision Resolution Strategy
- **Qualified Names Take Precedence**: `simpleitk.GaussianBlur` always resolves to the SimpleITK namespace, avoiding any ambiguity
- **Unqualified Name Resolution Order**: 
  1. User-defined constants and functions (highest priority)
  2. `default` namespace (for backward compatibility)
  3. Explicitly imported namespaces (in import order)
  4. Error if ambiguous between multiple namespaces
- **Explicit Resolution**: Users can always use qualified names to avoid ambiguity

## Performance Considerations
- **Lazy Loading**: Dynamic namespaces should only introspect libraries when first accessed to avoid startup overhead
- **Caching**: Introspection results should be cached to avoid repeated discovery overhead
- **Selective Exposure**: Support whitelist/blacklist patterns for large libraries like SimpleITK

## Error Handling Strategy
- **Graceful Degradation**: Failed dynamic namespaces should not break the entire system
- **Clear Error Messages**: Distinguish between "namespace not found" vs "function not available" 
- **Validation**: Dynamic primitives should validate their availability during registration
- **Fallback Mechanism**: Option to disable problematic dynamic namespaces

## Dynamic Function Validation
- **Runtime Type Adaptation**: Automatic conversion of basic constants between SimpleITK and VoxLogicA parameter formats
- **Runtime Validation**: Validate inputs/outputs in the `simpleitk` module where possible
- **Signature Inspection**: Use `inspect` module to validate function signatures during registration

## References
- See AGENT.md for project policies.
- See current implementation in `voxlogica/primitives/` and `voxlogica/execution.py`.
