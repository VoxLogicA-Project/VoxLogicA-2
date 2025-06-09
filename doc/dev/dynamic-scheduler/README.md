# Distributed Semantics for VoxLogica-2 Workplans

## Problem Statement

Design and implement a distributed execution semantics for VoxLogica-2 workplans (DAGs), enabling scalable, persistent, and memory-efficient computation across large and potentially distributed datasets.

## Requirements

- **Immutability:** All node results are immutable and content-addressed (SHA256).
- **Persistence:** Intermediate and final results are persisted to storage, not kept in RAM.
- **Concurrency:** Support for concurrent, thread-safe execution and storage operations.
- **Scalability:** Efficiently handle large DAGs (10K+ nodes) and datasets.
- **Extensibility:** Storage and execution layers must be abstract and replaceable.
- **Cross-platform:** Identical operation on macOS, Linux, and Windows.
- **Distributed/P2P Ready:** Storage and execution model must support future distributed and peer-to-peer extensions.
- **API:** All features accessible via CLI and HTTP API.

## Architectural Choices

- **Storage Backend:**
  - Use **SQLite** in WAL (Write-Ahead Logging) mode as the default persistent, immutable storage for all node results and metadata.
  - Binary data is content-addressed by SHA256 hash.
  - Design allows for alternative or hybrid backends (e.g., filesystem), but SQLite is the baseline.

- **Execution Engine:**
  - Compile VoxLogica-2 workplans (DAGs) directly to **Dask** lazy delayed graphs.
  - Dask manages parallel execution, dependency resolution, and efficient CPU utilization.
  - For language features like `foreach`, `accumulate`, or `mapreduce` over large datasets, compile these to Dask collections (arrays, dataframes, bags) and use Dask's lazy map/apply primitives, with persistence and deduplication handled via SQLite.

- **Future-Proofing:**
  - All storage is local-first and content-addressed, enabling future peer-to-peer sharing, deduplication, and distributed execution.
  - Abstract interfaces for storage and execution allow for backend replacement and language-agnostic integration.

---

This architecture enables robust, scalable, and distributed-ready execution of complex VoxLogica-2 workflows, with a clear path to advanced data-parallel primitives and distributed computation in future versions.