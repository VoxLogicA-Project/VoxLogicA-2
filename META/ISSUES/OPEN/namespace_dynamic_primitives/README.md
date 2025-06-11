# Issue: Namespace-based and Dynamic Primitive Loading for SimpleITK and Other Libraries

## Date
2025-06-11

## Linked Tasks
- [DESIGN.md](./DESIGN.md) (design and requirements)
- [PROMPT.md](./PROMPT.md) (implementation tasks and deliverables)

## Description
The current primitive system in VoxLogicA-2 requires one file per primitive in the `primitives/` directory. This approach is not scalable for large libraries like SimpleITK, which expose many useful functions. We propose to introduce a namespace abstraction for primitives, allowing both static (file-based) and dynamic (e.g., SimpleITK) primitive registration. This will enable dynamic discovery and registration of primitives from external libraries, reducing boilerplate and improving extensibility.

## Requirements
- Introduce a namespace concept for primitives, mapped to subdirectories under `primitives/`.
- Support both static (file-based) and dynamic (programmatic) primitive registration within a namespace.
- Implement a dynamic namespace for SimpleITK that exposes most or all SimpleITK functions as primitives, without requiring a file per function.
- Update the loader and documentation to support namespace-based and dynamic primitive loading.
- Maintain full backward compatibility with existing VoxLogicA programs and workflows.

## Status
- [ ] Open
- [x] Design complete
- [ ] Implementation pending

## Notes
- See DESIGN.md in this issue directory for requirements, ideas, and implementation sketch.
- See PROMPT.md for detailed implementation tasks and constraints.
- This issue is created in accordance with AGENT.md and project policies.
