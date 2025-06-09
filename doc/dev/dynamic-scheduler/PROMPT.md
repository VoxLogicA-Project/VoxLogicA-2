# Dynamic Scheduler with Database-Backed Memory Allocation - Design Prompt

## System Overview

Design a comprehensive execution semantics for VoxLogica-2 that implements a dynamic scheduler with database-backed memory allocation. This system will serve as an **alternative execution engine** for VoxLogica-2's DAG-based computation model, alongside the already described, but not yet designed or implemented, sequential execution approach. 

**Key Distinction**: This is an alternative to buffer allocation strategies - instead of managing buffers in memory, this approach persists intermediate results to storage with zero-copy, enabling distributed and peer-to-peer execution patterns and effectively leaving buffer management to the storage system itself.

## Core Requirements

### 1. DAG Node Architecture
- **Abstraction**: Should be freely replaceable with another implementation
- **Immutability**: Node results are immutable once computed and stored

### 2. Data Type Storage Strategy
- **Primitives**: Store strings, numbers, and booleans as JSON or native database types
- **Binary Data**: Store files and images as separate binary files or BLOBs with optional compression
- **Large Objects**: For large files, consider hybrid approach with memory-mapped files + metadata storage if the db is not efficient for large blobs
- **Nested Records**: Serialize complex data structures (JSON/MessagePack) while maintaining queryability where needed
- **Datasets**: Store dataset metadata with lazy-loading mechanisms for actual data
- **Simple Option**: Plain filesystem with JSON metadata files and binary data files is acceptable if it meets the performance and scalability requirements
- **Zero-Copy Approach**: Nodes should write directly to the database, minimizing memory overhead during storage operations

### 3. Performance Requirements
- **Streaming I/O**: Support streaming reads/writes for large binary objects
- **Concurrent Access**: Thread-safe operations with minimal locking overhead
- **Memory Efficiency**: Minimize memory footprint during storage/retrieval operations
- **Scalability**: Handle DAGs with 10K+ nodes efficiently

### 4. Storage Technology Options
- **Database Solutions**: SQLite, DuckDB, or embedded solutions preferred over client-server databases
- **Filesystem Solutions**: Plain filesystem with JSON metadata and binary files is an equally acceptable solution if it meets the requirements
- **Hybrid Approaches**: Combinations of database + filesystem as appropriate
- **NoSQL Options**: JSON-based databases like Couchbase Lite or PouchDB are welcome if other requirements are met
- **Peer-to-Peer Ready**: Storage format should support future distributed/peer-to-peer workload distribution
- **Works well with state-of-the-art schedulers**: such as Dask etc.
- **ACID Compliance**: Ensure data integrity during concurrent operations (where applicable)
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
1. **Storage Selection Criteria**: Evaluate and justify storage approach (database vs filesystem vs hybrid) based on read/write performance characteristics for our use case
2. **Storage Strategy Decisions**: Design decisions for when to use direct storage vs memory-mapped files vs hybrid approaches  
3. **Memory Management**: How the system will handle large objects without excessive memory allocation
4. **Concurrency Model**: Thread-safety and concurrent access patterns the system will support
5. **Distributed Readiness**: How the design supports future peer-to-peer distributed execution

### Integration Specifications
1. ** API Design**: Define API endpoints for node execution, result retrieval, and storage management
2. **DAG Node Execution**: Implementation of actual node execution (currently VoxLogica-2 only computes DAG structure)


## Deliverables Expected

1. **Architectural Design Document**: Complete system architecture with component diagrams
2. **Storage Schema**: Storage schema and documentation (database schema OR filesystem structure)
3. **API Specification**: Detailed API interface definitions with examples
4. **Performance Analysis**: Theoretical performance analysis and bottleneck identification  
5. **Implementation Plan**: Step-by-step implementation roadmap with milestones
6. **Testing Strategy**: Unit testing, integration testing, and performance testing approaches
7. **Deployment Guide**: Configuration, deployment, and operational procedures


## Relationship to Current VoxLogica-2 Implementation

### Current State
- **DAG Computation**: VoxLogica-2 currently computes DAG structure but does not execute nodes

## Success Criteria

- **Correctness**: All stored data can be retrieved exactly as stored
- **Performance**: Meets or exceeds specified performance benchmarks
- **Reliability**: System remains stable under high load and concurrent access
- **Maintainability**: Code is well-documented and easily extensible
- **Node Execution**: Successfully executes DAG nodes (beyond current structure-only computation)
- **Distributed Readiness**: Storage format supports future peer-to-peer distribution scenarios

## Constraints and Considerations

- **Memory Constraints**: Must work efficiently on systems with limited RAM
- **Disk Space**: Efficient storage utilization for large datasets
- **Network**: Should work without network dependencies (local-first)
- **Security**: Consider data encryption for sensitive datasets
- **Portability**: Avoid platform-specific dependencies where possible 



