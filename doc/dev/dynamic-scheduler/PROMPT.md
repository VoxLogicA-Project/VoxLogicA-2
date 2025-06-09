# Dynamic Scheduler with Database-Backed Memory Allocation - Design Prompt

## System Overview

Design a comprehensive execution semantics for VoxLogica-2 that implements a dynamic scheduler with persistent storage-backed execution. This system will serve as an **alternative execution engine** for VoxLogica-2's DAG-based computation model.

**Key Distinction**: This is an alternative to buffer allocation strategies - instead of managing buffers in memory, this approach persists intermediate results to storage, enabling distributed and peer-to-peer execution patterns and effectively leaving buffer management to the storage system itself.

## Core Requirements

### 1. DAG Node Architecture
- **Abstraction**: Should be freely replaceable with another implementation
- **Immutability**: Node results are immutable once computed and stored

### 2. Data Type Storage Strategy
- **Primitives**: Store strings, numbers, and booleans in chosen storage system
- **Binary Data**: Store binary files with SHA256 hashes as identifiers 
- **Nested Records**: Serialize complex data structures while maintaining queryability where needed
- **Datasets**: Store dataset metadata with lazy-loading mechanisms for actual data

### 3. Performance Requirements
- **Concurrent Access**: Thread-safe operations with minimal locking overhead
- **Memory Efficiency**: Minimize memory footprint during storage/retrieval operations
- **Scalability**: Handle DAGs with 10K+ nodes efficiently

### 4. Storage Technology Options
- **Database Solutions**: SQLite, DuckDB, or embedded solutions preferred over client-server databases
- **Filesystem Solutions**: Plain filesystem with metadata and binary files is an equally acceptable solution
- **Hybrid Approaches**: Combinations of database + filesystem as appropriate
- **Peer-to-Peer Ready**: Storage format should support future distributed/peer-to-peer workload distribution
- **Cross-Platform**: Must work identically on macOS, Linux, and Windows

### 5. Advanced Features
- **TTL/Eviction**: Optional time-based or size-based cache eviction policies

## Technical Specifications Required

### Architecture Design
1. **Storage Layer Interface**: Define abstract interface for storage operations
2. **Node Execution**: Implementation strategy for actual DAG node execution
3. **Caching Strategy**: In-memory caching approach on top of persistent storage

### Performance Design Goals
1. **Storage Selection**: Evaluate and justify storage approach based on performance characteristics
2. **Concurrency Model**: Thread-safety and concurrent access patterns
3. **Distributed Readiness**: How the design supports future peer-to-peer distributed execution

### Integration Specifications
1. **API Design**: Define API endpoints for node execution, result retrieval, and storage management
2. **VoxLogica-2 Integration**: Integration with existing VoxLogica-2 infrastructure


## Deliverables Expected

1. **Architectural Design Document**: Complete system architecture with component diagrams
2. **API Specification**: Detailed API interface definitions with examples
3. **Performance Analysis**: Theoretical performance analysis and bottleneck identification  
4. **Implementation Plan**: Step-by-step implementation roadmap with milestones
5. **Testing Strategy**: Unit testing, integration testing, and performance testing approaches
6. **Deployment Guide**: Configuration, deployment, and operational procedures


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
- **Network**: Should work without network dependencies (local-first)
- **Portability**: Avoid platform-specific dependencies where possible 



