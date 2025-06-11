# VoxLogicA-2: Dataset Loading and Processing Primitives

## Motivation
VoxLogicA-2 aims to support scalable, reproducible, and efficient processing of large datasets (potentially larger than memory) using a content-addressed, DAG-based execution model. This requires a robust set of primitives for dataset loading, lazy processing, and statistics, as well as careful handling of memoization and provenance.

## Design Goals
- **Zero-copy, lazy loading**: Datasets should be loaded and processed lazily, leveraging Dask for out-of-core and parallel computation.
- **Content-addressed memoization**: All operations (including dataset loading, mapping, reduction) are memoized using SHA256 hashes of their canonical JSON representation, recursively including dependencies.
- **Reproducibility**: Support for permanent, versioned dataset URIs (e.g., Zenodo DOIs) as stable dataset identities.
- **Composable primitives**: Users can build pipelines using map, reduce, filter, and statistics primitives.
- **Efficient storage**: Avoid per-element memoization; memoize at the operation/node level, with support for chunk/block-level granularity if needed.

## Proposed Primitives

### Dataset Loading
- `load_dataset(uri)`: Load a dataset from a permanent URI (e.g., Zenodo, S3, local file). Hash is based on the URI (if immutable) or content manifest.
- `from_files(file_list)`: Load a dataset from a list of files, with content-addressed hashing of the manifest.
- `from_zarr`, `from_hdf5`: Specialized loaders for common scientific formats.

### Dataset Processing
- `map(function, dataset)`: Lazily apply a function to each element/block of the dataset.
- `reduce(function, dataset)`: Reduce the dataset using a binary function (e.g., sum, mean).
- `filter(predicate, dataset)`: Select elements matching a predicate.
- `element(dataset, idx)`: Access a specific element (with proper hashing of the access operation).
- `shape(dataset)`, `dtype(dataset)`: Query dataset metadata.

### Statistics
- `mean(dataset)`, `std(dataset)`, `min(dataset)`, `max(dataset)`, `sum(dataset)`, `count(dataset)`, `percentile(dataset, p)`, `histogram(dataset, bins)`: Standard statistical operations, implemented lazily via Dask.

### Utility
- `persist(dataset)`, `compute(dataset)`: Materialize or persist a dataset in memory/disk.
- `concat(datasets)`, `split(dataset, ...)`: Combine or split datasets.

## Memoization and Provenance
- All primitives are memoized at the operation (node) level using content-addressed SHA256 IDs, recursively including all arguments and dependencies.
- For datasets loaded from permanent URIs, the hash is based on the URI; for local/mutable datasets, hash the content or manifest.
- Derived datasets (e.g., after map/reduce) are hashed as operations over their dependencies, preserving full provenance.
- Element/block-level memoization is not required unless chunk-level granularity is needed for very large datasets (can be added via Merkle tree structure if needed).

## CLI/Commands (Future)
- `voxlogica dataset info <uri>`: Show metadata, shape, dtype, hash, etc.
- `voxlogica dataset map <function> <uri>`: Apply a function to a dataset and output a new dataset.
- `voxlogica dataset stats <uri>`: Compute statistics on a dataset.

## Open Questions
- How to best represent chunked datasets for fine-grained memoization?
- Should we support partial materialization and distributed storage?
- How to handle mutable/ephemeral datasets in a reproducible way?

---

**Summary:**
This design enables scalable, reproducible, and efficient dataset processing in VoxLogicA-2, leveraging Dask, content-addressed memoization, and a rich set of composable primitives. It supports both local and published datasets, and lays the foundation for advanced distributed and out-of-core workflows.

---
Created automatically by GitHub Copilot on 2025-06-11.
