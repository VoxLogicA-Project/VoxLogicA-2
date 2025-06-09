# Dynamic Scheduler for VoxLogica-2

## Introduction

The Dynamic Scheduler is an execution engine for VoxLogica-2 that implements persistent, storage-backed execution of DAGs. Unlike traditional buffer-based approaches, it persists all intermediate results to storage, enabling scalable, memory-efficient, and distributed-ready computation. This design supports efficient execution of large DAGs, cross-platform operation, and future peer-to-peer workload distribution.

## Architectural Choices

### Storage Backend
- **SQLite** (in WAL mode) is the default persistent storage backend.
- All node results and metadata are stored in SQLite, with binary data content-addressed by SHA256 hash.
- The design allows for alternative or hybrid backends (e.g., filesystem), but SQLite is the recommended baseline.

### Node Execution and Scheduling
- Nodes are executed dynamically as soon as dependencies are satisfied.
- Results are immutable and content-addressed.
- All storage operations are atomic and thread-safe.

### API and Integration
- A well-defined, abstract storage interface enables backend replacement and language-agnostic access.
- All features are available via both CLI and HTTP API.

### Distributed/P2P Readiness
- Content-addressed storage (SHA256) enables future peer-to-peer sharing and deduplication.
- All operations are local-first, but the design is ready for distributed extensions.

## Why SQLite?

- **Cross-platform:** Works identically on macOS, Linux, and Windows.
- **No server required:** Embedded, no network or daemon needed.
- **Performance:** Satisfies high throughput and concurrency requirements for metadata and binary blobs.
- **Portability:** Single-file, easy to move or copy.
- **Reliability:** ACID-compliant, mature, and widely used.
- **Bundled with Python:** No user C compilation or external dependency management is required.
- **Extensible:** Can be replaced with other embedded databases if future requirements change.

SQLite is the only practical, portable, and robust choice that meets all requirements for performance, reliability, and future extensibility in this context.


### Implementation

A VoxLogica workplan (DAG) is compiled directly to a Dask lazy graph. Each node in the workplan is represented as a Dask delayed function, with dependencies corresponding to the DAG structure. Dask's scheduler manages parallel execution, efficiently utilizing all available CPU cores and handling dependencies automatically.

For constructs like "for each" loops over datasets, the dataset is represented as a Dask collection (such as a Dask array, dataframe, or bag). The body of the loop is compiled to a function, which is mapped over the collection using Dask's lazy map/apply methods. This enables chunked, parallel, and memory-efficient processing, as Dask only loads and processes data as needed.

Persistence is streamlined: each node execution checks SQLite (using SHA256 content addressing) for existing results before running, and stores new results upon completion. This ensures deduplication, robust recovery, and efficient storage. The combination of Dask for execution and SQLite for persistence provides scalable, fault-tolerant, and efficient execution of complex VoxLogica workflows, including those with large datasets and data-dependent control flow.