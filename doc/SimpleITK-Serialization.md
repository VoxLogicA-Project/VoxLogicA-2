# SimpleITK Image Serialization in VoxLogicA-2

## Overview
VoxLogicA-2 stores SimpleITK images in an SQLite database using Python's pickle serialization. This document details the serialization format, storage mechanism, and key characteristics discovered through analysis.

## Storage Backend

### Database Structure
- **Location**: `~/.voxlogica/storage.db`
- **Format**: SQLite with WAL (Write-Ahead Logging) mode
- **Table**: `results` 
  - `operation_id TEXT PRIMARY KEY` - SHA256 hash of operation
  - `data BLOB NOT NULL` - Pickled object data
  - `data_type TEXT NOT NULL` - Python type name
  - `created_at TIMESTAMP` - Creation timestamp
  - `size_bytes INTEGER` - Serialized size in bytes
  - `metadata TEXT` - Optional JSON metadata

### Current Storage Statistics
From analysis of existing database:
- **Image objects**: 2 stored
- **Average size**: 9.1 MB per image
- **Other data types**: float (21 bytes), NoneType (4 bytes)

## Serialization Mechanism

### Method
- **Primary**: Python's `pickle.dumps()` in `storage.py` line 128
- **Protocol**: Pickle protocol 4 (default)
- **Deserialization**: `pickle.loads()` in `storage.py` line 169

### SimpleITK Integration
SimpleITK images implement custom pickle support through:
- `__getstate__()` method returning `{'this': SwigPyObject}`
- `__setstate__()` method for reconstruction
- `__reduce__()` and `__reduce_ex__()` for pickle protocol support

### Serialization Process
1. **Object Creation**: `SimpleITK.SimpleITK.Image` constructor called with:
   - Dimensions tuple: `(188, 256, 190)` 
   - Component type: `sitkUInt8` (8-bit unsigned integer)
   - Components per pixel: 1

2. **State Serialization**: SWIG-wrapped C++ Image object serialized as SwigPyObject

3. **Pickle Structure**:
   ```
   PROTO 4
   FRAME 52
   SHORT_BINUNICODE 'SimpleITK.SimpleITK'
   SHORT_BINUNICODE 'Image'
   STACK_GLOBAL
   BININT1 188, BININT2 256, BININT1 190  # dimensions
   TUPLE3
   BININT1 1, BININT1 1                   # component info  
   TUPLE3
   REDUCE                                 # construct object
   [... pixel data follows ...]
   ```

## Data Characteristics

### Image Properties (Example from stored data)
- **Dimensions**: 188 × 256 × 190 voxels (9,144,320 total)
- **Pixel Type**: 8-bit unsigned integer
- **Spacing**: (0.88, 0.88, 0.88) mm
- **Origin**: (83.12, 118.12, -77.12) mm  
- **Direction**: Standard radiological (-1,0,0, 0,-1,0, 0,0,1)
- **Components**: 1 per pixel (grayscale)

### Storage Efficiency
- **Raw data size**: ~9.1 MB (9,144,320 bytes for pixel data)
- **Serialized size**: 9,145,688 bytes  
- **Overhead**: ~1,368 bytes (0.015%)
- **Compression**: None (pickle stores raw binary data)
- **Bytes per voxel**: ~1.0 (matches 8-bit pixel type)

### Binary Data Patterns
Analysis of pickle content shows:
- **Early chunks**: Mixed pixel values with 30-36% zeros
- **Later chunks**: Higher zero percentage (86-99%) indicating background regions
- **Distribution**: Typical medical imaging data with large background areas

## Serialization Features

### Roundtrip Integrity  
Testing confirms perfect roundtrip consistency:
- ✅ Dimensions preserved
- ✅ Spacing preserved  
- ✅ Pixel values preserved
- ✅ Metadata preserved
- ✅ Size identical on re-serialization

### SWIG Integration
SimpleITK uses SWIG to wrap C++ ITK (Insight Toolkit) objects:
- **Native object**: `itk::simple::Image *` C++ pointer
- **Python wrapper**: SwigPyObject containing native pointer
- **Serialization**: SWIG handles automatic serialization/deserialization
- **Memory management**: Automatic cleanup via SWIG reference counting

## Performance Implications

### Storage Size
- Large images (9+ MB) require significant database space
- No automatic compression applied
- WAL mode provides concurrent access but increases disk usage

### Serialization Speed
- Pickle protocol 4 optimized for large binary data
- SWIG serialization handles complex C++ object state
- Single-pass serialization for entire image

### Memory Usage
- Full image loaded into memory during serialize/deserialize
- No streaming or chunked processing
- Suitable for typical medical imaging workflows

## Integration with VoxLogicA-2

### Content-Addressed Storage
- Images cached by operation SHA256 hash
- Immutable storage prevents data corruption
- Enables efficient duplicate detection

### Type Safety
- `data_type` field stores 'Image' for SimpleITK objects
- Enables type-specific retrieval logic
- Supports heterogeneous result storage

### Metadata Support
- Optional JSON metadata field available
- Currently unused for Image objects
- Could store image provenance, processing history

## Technical Notes

### Dependencies
- **SimpleITK**: Provides Image class and serialization methods
- **SWIG**: Enables Python/C++ object bridging
- **pickle**: Python standard library serialization
- **SQLite**: Persistent storage backend

### Thread Safety
- SQLite WAL mode supports concurrent readers
- Pickle operations are thread-safe for immutable data
- Storage backend handles connection pooling

### Cross-Platform Compatibility
- Pickle protocol 4 standardized across Python versions
- SimpleITK provides consistent serialization across platforms
- SQLite database portable between operating systems

## Future Considerations

### Optimization Opportunities
1. **Compression**: Apply compression to reduce storage size
2. **Streaming**: Implement chunked serialization for very large images
3. **Metadata**: Utilize metadata field for image provenance
4. **Caching**: Implement LRU cache for frequently accessed images

### Monitoring
- Track storage growth over time
- Monitor serialization/deserialization performance
- Profile memory usage for large datasets
