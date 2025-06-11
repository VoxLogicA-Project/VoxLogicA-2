# DESIGN: Save Command Image Inconsistency Resolution

## Issue Status
**OPEN** - Design and requirements gathering phase

## Date Created
2025-06-11

## Problem Summary
Inconsistency between database storage (9MB pickled image blobs) and save command output (1KB text metadata) for SimpleITK images.

## Design Decisions Made

### 1. Backwards Compatibility ❌
**Decision**: We will NOT maintain current text-only save behavior
**Rationale**: Software is in pre-alpha with no production users, breaking changes acceptable

### 2. File Extension-Based Format Selection ❓
**Status**: Under discussion
**Question**: Should file extensions drive serialization format selection?

### 3. Image Detection Strategy ❓
**Status**: Needs discussion and technical investigation
**Question**: Should `save` automatically detect image objects and use appropriate serialization?

## Technical Investigation Required

### Pickle Understanding ✅
**What is pickle?**
- Python's binary serialization protocol for converting objects to/from binary format
- Preserves object type, state, and references
- Uses protocol 4 by default, handles complex objects with custom `__getstate__`/`__setstate__`

**How does SimpleITK customize pickle?**
- SimpleITK images implement custom pickle methods: `__getstate__`, `__setstate__`, `__reduce__`, `__reduce_ex__`
- `__getstate__()` returns `{'this': SwigPyObject}` containing C++ `itk::simple::Image*` pointer
- SWIG automatically handles serialization of the underlying C++ ITK object
- All image data (voxels + metadata) preserved in the C++ object state
- Binary state embedded in pickle stream, deserialization reverses via `__setstate__()`

**Role of SWIG:**
- SWIG = Simplified Wrapper and Interface Generator
- Wraps C++ ITK library for Python access
- Creates Python bindings for C++ classes/functions
- Handles C++/Python object bridge and memory management
- Enables automatic serialization of complex C++ objects through Python pickle

### Database vs Save Command Analysis ✅
**Database (correct)**: Uses `pickle.dumps(image)` → full 9MB binary blob with all data
**Save command (inconsistent)**: Uses `str(image)` → 1KB text metadata only

### Format Options Analysis
- Native SimpleITK formats
- Medical imaging standards
- Binary vs text representations

## Pre-Alpha Breaking Changes Policy
**Added to README**: This software is in pre-alpha stage and we constantly make breaking changes as there are no users yet.

## Next Steps
1. Investigate pickle, SITK, and SWIG relationship
2. Analyze file extension-based format selection feasibility
3. Design automatic image detection strategy
4. Propose unified serialization approach

## Open Questions
- Should `save "image.bin"` use pickle (like database) or binary format?
- How to handle different image formats (.nii.gz, .mha, etc.)?
- Should metadata export be a separate command?

---
*This document will be updated as design decisions are made*
