# Issue: Investigate Image Compression for Database Storage

## Date
2025-06-11

## Status
**OPEN** - Investigation Required

## Priority
Low - Performance optimization opportunity

## Description
SimpleITK images are currently stored in the database as large uncompressed pickle blobs. Each Image object averages ~9.1MB in storage, which represents raw binary image data serialized through Python's pickle format. This raises questions about storage efficiency and whether compression could significantly reduce database size and improve I/O performance.

## Problem Statement
Investigation needed to determine:
1. **Current compression status**: Are SimpleITK/ITK images already compressed internally?
2. **Compression opportunities**: What compression ratio could be achieved on the pickle blobs?
3. **Performance implications**: Trade-offs between storage size, compression/decompression time, and memory usage
4. **Implementation options**: Where and how to apply compression in the storage pipeline

## Evidence from Serialization Analysis
From `/META/DOCUMENTATION/SimpleITK-Serialization.md`:
- **Storage size**: 9,145,688 bytes for 188×256×190 image (9,144,320 voxels)
- **Efficiency**: ~1.0 bytes per voxel (matches 8-bit pixel type)
- **Format**: Raw pickle protocol 4 with no compression
- **Content patterns**: High zero percentage in background regions (86-99% zeros in later chunks)

## Investigation Areas

### 1. Compression Feasibility Analysis
- **Medical imaging patterns**: High zero percentage in background suggests good compression potential
- **Pickle format**: Binary data sections may compress well with standard algorithms
- **ITK internal format**: Determine if ITK already applies compression internally

### 2. Compression Methods Evaluation
- **Zlib/GZip**: Standard Python compression libraries
- **LZ4**: Fast compression for real-time scenarios
- **Brotli**: High compression ratio option
- **Medical-specific**: DICOM-style compression algorithms

### 3. Storage Pipeline Integration Points
- **Pre-storage**: Compress before `pickle.dumps()`
- **Post-pickle**: Compress the pickled blob before database insertion
- **Database-level**: SQLite compression extensions
- **Hybrid**: Selective compression based on data size thresholds

### 4. Performance Impact Assessment
- **Compression time**: Measure compression/decompression overhead
- **Memory usage**: Peak memory during compression operations
- **Storage reduction**: Actual compression ratios achieved
- **Cache effects**: Impact on retrieval performance

## Current Storage Statistics
- **Total Images**: 2 stored in database
- **Average size**: 9.1MB per image (uncompressed)
- **Storage backend**: SQLite with WAL mode
- **Serialization**: `pickle.dumps()` with protocol 4

## Investigation Questions
1. What compression ratio can be achieved on typical medical imaging data?
2. Does the compression time overhead justify the storage savings?
3. Would compression improve or hurt cache performance?
4. Should compression be configurable or automatic?
5. What threshold size should trigger compression?

## Implementation Considerations
- **Backwards compatibility**: Ensure existing data remains accessible
- **Migration**: Strategy for compressing existing stored images
- **Configuration**: User-configurable compression levels/algorithms
- **Error handling**: Robust fallback for compression failures

## Success Criteria
- **Quantified benefit**: Compression ratio and performance impact measured
- **Implementation recommendation**: Clear guidance on whether/how to implement
- **Benchmark results**: Performance comparison with/without compression
- **Migration strategy**: Plan for upgrading existing installations

## Related Components
- `voxlogica/storage.py` - Storage backend implementation
- `META/DOCUMENTATION/SimpleITK-Serialization.md` - Technical analysis of current format
- Content-addressed storage system using SHA256 operation IDs

## Future Considerations
- **Distributed storage**: Compression benefits for network transfer
- **Cloud deployment**: Storage cost optimization
- **Large datasets**: Scalability with many/large images
- **Streaming**: Support for compressed streaming access

## Technical Context
Current storage path: SimpleITK Image → `pickle.dumps()` → SQLite BLOB → File system

Investigation will determine optimal insertion point for compression and establish performance baselines for informed decision-making.
