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
- [x] **IMPLEMENTATION COMPLETED**
- [x] Design complete
- [x] Parser grammar updated to support qualified identifiers
- [x] Namespace system implemented and working
- [x] CLI `list-primitives` command implemented

## Implementation Status
### ✅ **COMPLETED** - Core Implementation
- [x] **Parser Grammar** - Updated to support qualified identifiers (`simpleitk.load_sitk_image`)
- [x] **Namespace System** - Complete refactor of PrimitivesLoader with namespace support
- [x] **Dynamic SimpleITK** - SimpleITK namespace with dynamic primitive loading
- [x] **Default Namespace** - Basic arithmetic and logic primitives
- [x] **Test Namespace** - Existing test primitives moved
- [x] **Import System** - Enhanced to support namespace imports
- [x] **CLI Command** - `./voxlogica list-primitives [namespace]` implemented
- [x] **Full Testing** - Complete workflow tested with `test_sitk.imgql`

### ✅ **VERIFIED WORKING** 
- [x] SimpleITK primitives execute correctly with qualified names
- [x] Default namespace arithmetic works for unqualified operators
- [x] Namespace collision resolution works correctly  
- [x] Backward compatibility maintained
- [x] Dynamic primitive introspection works with detailed descriptions

## Test Results
```bash
# Parser supports qualified identifiers
./voxlogica run test_sitk.imgql --execute
# Output: Execution completed successfully! Operations completed: 4

# List-primitives command works
./voxlogica list-primitives
# Shows all primitives from all namespaces

./voxlogica list-primitives simpleitk  
# Shows only SimpleITK primitives with descriptions
```

## Notes
- See DESIGN.md in this issue directory for requirements, ideas, and implementation sketch.
- See PROMPT.md for detailed implementation tasks and constraints.
- This issue is created in accordance with AGENT.md and project policies.
