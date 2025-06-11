# Implementation Prompt: Namespace-based and Dynamic Primitive Loading

## Context
This prompt references the design and requirements in DESIGN.md in this directory. The goal is to refactor the VoxLogicA-2 primitive system to support namespaces, dynamic loading, and improved discoverability, as outlined in the design document.

## Tasks
1. **Namespace Refactor**
   - Implement the namespace abstraction for primitives as described in DESIGN.md.
   - Each namespace is a subdirectory under `primitives/` and can be static (file-based) or dynamic (programmatic registration).

2. **Move Existing Primitives**
   - Move all current non-basic primitives to a new namespace called `test` (i.e., `primitives/test/`).
   - Basic arithmetic and logic primitives (addition, multiplication, etc.) should be moved to the `default` namespace as described in task 3.

3. **Default Namespace**
   - Create a `default` namespace containing basic arithmetic and logic primitives (e.g., addition, subtraction, multiplication, division, logical and/or/not, etc.).
   - Move existing basic arithmetic primitives from the current primitives directory to this namespace.
   - Ensure these are available as unqualified operators for backward compatibility.

4. **SimpleITK Namespace**
   - Create a `simpleitk` namespace that dynamically exposes most or all SimpleITK functions as primitives.
   - Use Python introspection to enumerate functions and wrap them to conform to the primitive interface.
   - For help and listing, extract docstrings from the SimpleITK library for each function.

5. **Enhanced Primitives Listing and Help System**
   - Each namespace must implement a `list_primitives()` method that returns the available primitives and their descriptions (where available).
   - Implement `./voxlogica list-primitives [namespace]` CLI command for listing available primitives
   - Support `help <operator>` in ImgQL for showing operator documentation
   - Auto-generate documentation from docstrings for dynamic primitives
   - Provide namespace-level documentation via `__doc__` strings in `__init__.py`

6. **Testing Requirements**
   - **Namespace Loading Tests**: Verify correct namespace discovery and loading
   - **Dynamic Registration Tests**: Test SimpleITK function wrapping and execution  
   - **Collision Resolution Tests**: Verify namespace priority and qualified name resolution
   - **Backward Compatibility Tests**: Ensure existing workflows continue working
   - **Performance Tests**: Validate lazy loading and caching behavior
   - All tests should be placed in appropriate subdirectories under `tests/` following existing project structure

7. **Documentation**
   - Update or add documentation as needed to describe the new system, referencing DESIGN.md for architectural details.

## Constraints
- Follow all project policies in AGENT.md and the META directory.
- Do not break backward compatibility for existing workflows.
- Ensure all code is type-annotated and documented.
- Use only the allowed directories for new files (see AGENT.md).

## References
- DESIGN.md (this directory): Full requirements and design sketch.
- AGENT.md: Project policies and coding conventions.
- Existing primitives and loader implementation in `voxlogica/primitives/` and `voxlogica/execution.py`.

## Deliverables
- Refactored codebase with namespace support and dynamic SimpleITK primitive loading.
- All existing primitives moved to the `test` namespace.
- New `default` and `simpleitk` namespaces as described.
- Working `list_primitives` and help features with CLI support.
- Comprehensive test suite covering all new functionality.
- Updated documentation.

This prompt is intended for an AI agent or developer to implement the full feature as described, referencing the design and following all project conventions.
