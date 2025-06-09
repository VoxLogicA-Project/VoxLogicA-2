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
