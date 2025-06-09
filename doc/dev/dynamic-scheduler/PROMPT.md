# Dynamic Scheduler with Database-Backed Memory Allocation - Design Prompt

## System Overview

Design a comprehensive execution semantics for VoxLogica-2 that implements a dynamic scheduler with database-backed memory allocation. This system will serve as the core execution engine for VoxLogica-2's DAG-based computation model, providing efficient caching, persistence, and memory management for large-scale image processing and machine learning workflows.

## Core Requirements

### 1. DAG Node Architecture
- **Abstraction**: Should be freely replaceable with another implementation
- **Immutability**: Node results are immutable once computed and stored

### 2. Data Type Storage Strategy
- **Primitives**: Store strings, numbers, and booleans directly in the database as native types
- **Binary Data**: Store files and images as BLOBs with optional compression
- **Large Objects**: For files >1MB, consider hybrid approach with memory-mapped files + database metadata
- **Nested Records**: Serialize complex data structures (JSON/MessagePack) while maintaining queryability
- **Datasets**: Store dataset metadata in DB, with lazy-loading mechanisms for actual data

### 3. Performance Requirements
- **Streaming I/O**: Support streaming reads/writes for large binary objects
- **Concurrent Access**: Thread-safe operations with minimal locking overhead
- **Memory Efficiency**: Minimize memory footprint during storage/retrieval operations
- **Scalability**: Handle DAGs with 10K+ nodes efficiently

### 4. Database Technology Constraints
- **Serverless Preferred**: SQLite, DuckDB, or embedded solutions preferred over client-server databases
- **Works well with state-of-the-art schedulers**: such as Dask etc.
- **NOSQL is also very welcome**: JSON-based databases like Couchbase Lite or PouchDB are equally good if the other requirements are met
- **ACID Compliance**: Ensure data integrity during concurrent operations
- **Cross-Platform**: Must work identically on macOS, Linux, and Windows
- **Backup/Recovery**: Support database backup and restoration mechanisms
- **Schema Evolution**: Database schema must be versioned and upgradeable
- **zero-copy approach**: even for writing to the database: ideally nodes write directly to it

### 5. Advanced Storage Optimization
- **TTL/Eviction**: Optional time-based or size-based cache eviction policies

## Technical Specifications Required

### Architecture Design
1. **Storage Layer Interface**: Define abstract interface for storage operations (get, set, delete, exists, list)
2. **Database Schema**: Complete database schema, if SQL, with tables, indexes, and constraints (or nosql equivalent)
3. **Node Serialization**: Serialization format for different data types
4. **Cache Policies**: In-memory caching strategy on top of persistent storage should be implicit in the chosen db architecture (see "zero-copy approach" above)

### Performance Design Goals
1. **Database Selection Criteria**: Evaluate and justify database choice based on read/write performance characteristics for our use case
2. **Storage Strategy Decisions**: Design decisions for when to use direct DB storage vs memory-mapped files vs hybrid approaches
3. **Memory Management**: How the system will handle large objects without excessive memory allocation
4. **Concurrency Model**: Thread-safety and concurrent access patterns the system will support

### Integration Specifications
1. **VoxLogica-2 API**: How the storage system integrates with the existing execution engine


## Deliverables Expected

1. **Architectural Design Document**: Complete system architecture with component diagrams
2. **Database Schema**: schema and schema documentation
3. **API Specification**: Detailed API interface definitions with examples
4. **Performance Analysis**: Theoretical performance analysis and bottleneck identification
5. **Implementation Plan**: Step-by-step implementation roadmap with milestones
6. **Testing Strategy**: Unit testing, integration testing, and performance testing approaches
7. **Deployment Guide**: Configuration, deployment, and operational procedures

## Success Criteria

- **Correctness**: All stored data can be retrieved exactly as stored
- **Performance**: Meets or exceeds specified performance benchmarks
- **Reliability**: System remains stable under high load and concurrent access
- **Maintainability**: Code is well-documented and easily extensible
- **Integration**: Seamlessly integrates with existing VoxLogica-2 codebase

## Constraints and Considerations

- **Memory Constraints**: Must work efficiently on systems with limited RAM
- **Disk Space**: Efficient storage utilization for large datasets
- **Network**: Should work without network dependencies (local-first)
- **Security**: Consider data encryption for sensitive datasets
- **Portability**: Avoid platform-specific dependencies where possible 



