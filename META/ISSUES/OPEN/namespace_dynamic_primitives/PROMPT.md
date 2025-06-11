# Implementation Prompt: Namespace-based and Dynamic Primitive Loading

## Context
This prompt references the design and requirements in DESIGN.md in this directory. The goal is to refactor the VoxLogicA-2 primitive system to support namespaces, dynamic loading, and improved discoverability, as outlined in the design document.

## Tasks
1. **Namespace Refactor**
   - Implement the namespace abstraction for primitives as described in DESIGN.md.
   - Each namespace is a subdirectory under `primitives/` and can be static (file-based) or dynamic (programmatic registration).

2. **Move Existing Primitives**
   - Move all current primitives to a new namespace called `test` (i.e., `primitives/test/`).

3. **Default Namespace**
   - Create a `default` namespace containing basic arithmetic and logic primitives (e.g., addition, subtraction, multiplication, division, logical and/or/not, etc.).
   - Ensure these are available as unqualified operators for backward compatibility.

4. **SimpleITK Namespace**
   - Create a `SimpleITK` namespace that dynamically exposes most or all SimpleITK functions as primitives.
   - Use Python introspection to enumerate functions and wrap them to conform to the primitive interface.
   - For help and listing, extract docstrings from the SimpleITK library for each function.

5. **Primitives Listing and Help**
   - Each namespace must implement a `list_primitives()` method that returns the available primitives and their descriptions (where available).
   - Implement a help feature in the loader/system that allows users to list all available primitives (optionally by namespace) and view descriptions/docstrings.

6. **Documentation**
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
- New `default` and `SimpleITK` namespaces as described.
- Working `list_primitives` and help features.
- Updated documentation.

This prompt is intended for an AI agent or developer to implement the full feature as described, referencing the design and following all project conventions.
