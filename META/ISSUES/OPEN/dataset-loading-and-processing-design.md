# VoxLogicA-2: Dataset Loading and Processing Primitives

## Motivation
VoxLogicA-2 aims to support scalable, reproducible, and efficient processing of large datasets (potentially larger than memory) using a content-addressed, DAG-based execution model. This requires a robust set of primitives for dataset loading, lazy processing, and statistics, as well as careful handling of memoization and provenance.

## Design Goals
- **Zero-copy, lazy loading**: Datasets should be loaded and processed lazily, leveraging Dask for out-of-core and parallel computation.
- **Content-addressed memoization**: All operations (including dataset loading, mapping, reduction) are memoized using SHA256 hashes of their canonical JSON representation, recursively including dependencies.
- **Reproducibility**: Support for permanent, versioned dataset URIs (e.g., Zenodo DOIs) as stable dataset identities.
- **Composable primitives**: Users can build pipelines using map, reduce, filter, and statistics primitives.
- **Efficient storage**: Support both operation-level and per-element memoization for comprehensive result caching.

## Implementation Strategy

### Minimum Viable Prototype (MVP)
The core functionality required for a working prototype includes:
1. **Map Operations**: Essential for applying functions across dataset elements
2. **Statistics Operations**: Essential for data analysis and validation
3. **Dataset Loading**: Foundation primitive for all dataset operations

Additional primitives can be implemented incrementally after the MVP is established.

## Proposed Primitives

### Dataset Loading
- `load_dataset(uri)`: Load a dataset from a permanent URI (e.g., Zenodo, S3, local file). Hash is based on the URI (if immutable) or content manifest.
- `from_files(file_list)`: Load a dataset from a list of files, with content-addressed hashing of the manifest.
- `from_zarr`, `from_hdf5`: Specialized loaders for common scientific formats.

### Dataset Processing (Essential for MVP)
- `map(function, dataset)`: Lazily apply a function to each element/block of the dataset. Core primitive for minimum viable prototype.
- `reduce(function, dataset)`: Reduce the dataset using a binary function (e.g., sum, mean).
- `filter(predicate, dataset)`: Select elements matching a predicate.
- `element(dataset, idx)`: Access a specific element (with proper hashing of the access operation).
- `shape(dataset)`, `dtype(dataset)`: Query dataset metadata.

### Statistics (Essential for MVP)
- `mean(dataset)`, `std(dataset)`, `min(dataset)`, `max(dataset)`, `sum(dataset)`, `count(dataset)`: Core statistical operations for minimum viable prototype, implemented lazily via Dask.
- `percentile(dataset, p)`, `histogram(dataset, bins)`: Additional statistical operations for enhanced functionality.

### Utility
- `persist(dataset)`, `compute(dataset)`: Materialize or persist a dataset in memory/disk.
- `concat(datasets)`, `split(dataset, ...)`: Combine or split datasets.

## Memoization and Provenance

### Operation-Level Memoization
- All primitives are memoized at the operation (node) level using content-addressed SHA256 IDs, recursively including all arguments and dependencies.
- Dataset operations (e.g., `map`, `reduce`) are memoized using syntactic hashing, consistent with existing VoxLogicA-2 principles.

### URI-Based Dataset Hashing  
- For datasets loaded from permanent URIs, the hash is based on the URI (for immutable, fast identification).
- This approach is consistent with VoxLogicA-2's syntactic hashing principles (e.g., `f(x)` and `g(x)` get different hashes even with potentially same results).
- Content-based hashing can be implemented alongside URI hashing as a future enhancement.

### Per-Element Memoization
- Individual element applications within dataset operations are also memoized and stored.
- When map operations are compiled, nodes are created lazily but individual element results are added to storage.
- Collection hashing strategy (e.g., hashing the list of element hashes) requires further technical discussion.

### Provenance Tracking
- Derived datasets (e.g., after map/reduce) are hashed as operations over their dependencies, preserving full provenance.
- Both operation-level and element-level results maintain complete computational lineage.

## Dask Graph Integration for Dataset Processing

VoxLogicA-2 implements parallel execution within workflows, enabling full utilization of Dask's parallelism and lazy evaluation capabilities. When compiling VoxLogicA-2 programs to Dask, dataset operations such as `map`, `reduce`, and `filter` are represented as nodes in the Dask graph. For expressions like:

```voxlogica
let h(x) = f(g(x))
map(h, load_dataset(URI))
```

- The `load_dataset(URI)` node produces a Dask collection (e.g., dask.array, dask.bag).
- The function `h(x)` is compiled as a Dask subgraph (using `dask.delayed` or equivalent), representing the computation `f(g(x))`.
- The `map` primitive is compiled to a Dask mapping operation (e.g., `map`, `map_blocks`, or `map_partitions`), applying the subgraph for `h` to each element or chunk of the dataset.
- Dask's scheduler is informed that `h` is a Dask graph, enabling full task parallelism: each element/chunk is processed in parallel, and the internal structure of `h` can itself be parallelized if it is complex.
- The resulting Dask graph is compact and lazy: it contains a node for loading the dataset and a parallel set of subgraphs for each mapped element/chunk, all scheduled efficiently by Dask. No manual expansion for every element is needed.

**This approach ensures that VoxLogicA-2 fully leverages Dask's parallelism and lazy evaluation, supporting scalable, efficient, and composable dataset processing workflows.**

## CLI/Commands (Future)
- `voxlogica dataset info <uri>`: Show metadata, shape, dtype, hash, etc.
- `voxlogica dataset map <function> <uri>`: Apply a function to a dataset and output a new dataset.
- `voxlogica dataset stats <uri>`: Compute statistics on a dataset.

## Open Questions

### Collection Hashing Strategy
- How to efficiently hash collections of elements? 
- Potential approach: Hash collections by hashing the list of element hashes
- Requires technical discussion and performance evaluation

### Dask Collection Type Selection  
- Criteria for selecting `dask.array` vs `dask.bag` vs other collection types
- Type consistency guarantees across operations
- Compatibility with existing VoxLogicA-2 type system

### Implementation Details
- How to best represent chunked datasets for fine-grained memoization?
- Should we support partial materialization and distributed storage?
- How to handle mutable/ephemeral datasets in a reproducible way?

---

**Summary:**
This design enables scalable, reproducible, and efficient dataset processing in VoxLogicA-2, leveraging Dask's parallel execution capabilities, content-addressed memoization with both operation-level and per-element storage, and a rich set of composable primitives. The design is compatible with VoxLogicA-2's parallel execution model and existing SHA256-based memoization system. It supports both URI-based and future content-based dataset identification, and lays the foundation for advanced distributed and out-of-core workflows.

**Key Design Principles:**
- Dual-level memoization (operation and element)
- URI-based hashing consistent with syntactic hashing principles  
- Parallel execution within workflows via Dask integration
- Incremental implementation starting with MVP (map + statistics + loading)

---
Updated: 2025-06-12 (Clarifications based on technical analysis)
