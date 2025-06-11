# Issue: Inconsistency Between Database Storage and Save Command for Images

## Date
2025-06-11

## Status
**OPEN** - Investigation Required

## Priority
Medium - Affects user workflow and data integrity expectations

## Description
There is a significant inconsistency between how SimpleITK images are stored in the database versus how they are saved to disk via the `save` command. The database stores full binary image data (~9MB pickled blobs), while the `save` command creates small text files (~1KB) containing only the string representation of the image metadata.

## Problem Statement
When users execute:
```imgql
let img = ReadImage("tests/chris_t1.nii.gz")
let thresholded = Threshold(img, threshold_value)
save "test.bin" thresholded
```

They expect `test.bin` to contain the actual image data, but instead get a text description like:
```
Image (0x1528335f0)
  RTTI typeinfo:   itk::Image<unsigned char, 3u>
  Reference Count: 1
  Modified Time: 1643
  ...
  LargestPossibleRegion: 
    Dimension: 3
    Index: [0, 0, 0]
    Size: [188, 256, 190]
```

## Evidence

### Database Storage (Correct)
- **Size**: 9,145,688 bytes (full image data)
- **Format**: Pickled SimpleITK Image object with binary voxel data
- **Content**: Complete image with all pixel values preserved
- **Location**: `~/.voxlogica/storage.db` via `pickle.dumps(image)`

### Save Command (Inconsistent)
- **Size**: 1,222 bytes (metadata only)
- **Format**: ASCII text via `str(image)`
- **Content**: ITK object metadata description
- **Location**: User-specified file via `save` command

### Root Cause Analysis
In `/implementation/python/voxlogica/execution.py` line 510-517:
```python
def _save_result_to_file(self, result, filename: str):
    # ...
    else:  # txt format or no extension
        with open(filepath, 'w') as f:
            f.write(str(result))  # <-- Problem: str(image) only gives metadata
```

## User Impact
1. **Data Loss**: Actual image data is not saved to disk
2. **Workflow Disruption**: Users cannot use saved files in external tools
3. **Misleading Behavior**: Operation reports success but produces unusable output
4. **Inconsistent Semantics**: Database preserves data, `save` command doesn't

## Investigation Questions

### 1. Intended Behavior Clarification
- **Should** `save` store the full image data or just metadata?
- **Should** `save` format detection be based on file extension?
- **Should** image-specific file formats be supported (e.g., .nii.gz, .nii, .mha)?

### 2. Format Support Options
- **Binary formats**: Pickle (.pkl), direct binary dump
- **Medical formats**: NIfTI (.nii.gz), MetaImage (.mha), DICOM
- **Standard formats**: JSON (metadata + base64 data), XML
- **Text formats**: Keep current behavior for debugging?

### 3. Consistency Strategy
- **Unified approach**: Both database and save use same serialization
- **Format-specific**: Different serialization based on output format
- **User choice**: Allow users to specify serialization method

### 4. Backwards Compatibility
- **Existing workflows**: How to handle current save behavior expectations
- **Migration**: Strategy for users depending on current text output
- **Configuration**: Optional behavior modes

## Technical Investigation Needed

### 1. Current Save Command Flow
```
goal.operation == 'save' 
→ _execute_goal_with_result() 
→ _save_result_to_file() 
→ str(result) for non-json/pickle extensions
```

### 2. SimpleITK Serialization Options
- **Native ITK formats**: WriteImage functionality already available
- **Pickle preservation**: Use same method as database storage
- **Binary dump**: Direct voxel data extraction
- **Metadata extraction**: Current behavior but with structure

### 3. Extension Detection Logic
Current logic in `_save_result_to_file()`:
- `.json` → JSON with WorkPlanJSONEncoder
- `.pkl/.pickle` → Pickle binary format
- **Everything else** → `str(result)` text format

## Proposed Investigation Approach

### Phase 1: Requirements Analysis
1. **Survey user expectations**: What should `save "image.bin"` produce?
2. **Review SimpleITK capabilities**: Available serialization methods
3. **Assess format ecosystem**: Standard medical imaging formats

### Phase 2: Technical Options Evaluation
1. **Extend format detection**: Add medical imaging formats
2. **Enhance save logic**: Image-aware serialization
3. **Consistency alignment**: Match database storage approach
4. **Performance testing**: Compare serialization methods

### Phase 3: Implementation Strategy
1. **Backwards compatibility**: Preserve existing behavior option
2. **Format flexibility**: Support multiple output formats
3. **User configuration**: Allow save behavior customization
4. **Error handling**: Robust format detection and fallback

## Related Issues
- **Content-addressed storage side-effects**: `META/ISSUES/OPEN/content-addressed-storage-side-effects/`
- **Image compression investigation**: `META/ISSUES/OPEN/image-compression-database-storage/`

## Files Involved
- `implementation/python/voxlogica/execution.py` - Save command implementation
- `implementation/python/voxlogica/storage.py` - Database storage (working correctly)
- `implementation/python/voxlogica/primitives/simpleitk/` - SimpleITK integration

## Success Criteria
1. **Consistent behavior**: Database and save command handle images uniformly
2. **User expectations**: Saved files contain usable image data
3. **Format flexibility**: Support for common medical imaging formats
4. **Backwards compatibility**: Existing workflows continue to work
5. **Clear documentation**: Users understand save command behavior

## Discussion Points for Resolution
1. Should the save command automatically detect image objects and use WriteImage?
2. Should file extensions drive serialization format selection?
3. Should there be a separate command for metadata export vs. data export?
4. How to balance flexibility with user-friendly defaults?

This issue requires discussion to determine the intended behavior before implementing technical solutions.
