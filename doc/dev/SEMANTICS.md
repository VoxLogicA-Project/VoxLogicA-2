# SEMANTICS.md

## VoxLogicA Language Semantics: Datasets, Images, and Workplans

### Motivation

VoxLogicA aims to provide a high-level, declarative language for manipulating and analyzing large datasets of images (e.g., 3D MRI scans) and other artifacts. The language must support scalable, memory-efficient operations, and seamless integration with machine learning workflows (training, prediction).

---

## Requirements

### 1. Dataset Abstraction

- Datasets may be large (e.g., many 3D images) and cannot always be loaded fully in memory.
- The language must provide abstractions for datasets, supporting lazy loading, streaming, and chunked processing.
- Datasets may contain images, booleans, strings, numbers, and nested records.

### 2. Image and Primitive Types

- Native support for images (2D, 3D, multi-channel), booleans, strings, numbers, and records (including nested records).
- Images may be large (e.g., 3D MRI) and should be manipulated via efficient libraries (e.g., SimpleITK).
- The language should allow manipulation of both individual images and entire datasets.

### 3. Workplan (DAG) Semantics

- The language should represent computations as a Directed Acyclic Graph (DAG) or workplan.
- Nodes in the DAG represent operations (e.g., image processing, dataset transformation, ML training/prediction).
- Edges represent data dependencies.
- The system should optimize execution (e.g., avoid recomputation, parallelize where possible).

### 4. Machine Learning Integration

- The language must support invoking training and prediction using external tools (e.g., nnUNet) in a portable way.
- Training and prediction should operate on datasets, with results tracked and available for further processing.
- The system should abstract over the details of tool invocation, data preparation, and result collection.

### 5. Extensibility

- New primitives (e.g., image operations, dataset transformations) should be easy to add, ideally as plugins or features.
- The system should support integration with additional libraries (e.g., SimpleITK, PyTorch, etc.).

---

## Example Use Cases

- Load a dataset of 3D MRI scans, apply preprocessing, and train a segmentation model.
- Concatenate two datasets, augment images, and run predictions using a trained model.
- Compute statistics (mean, std) over large image datasets without loading all data in memory.

---

## Initial Implementation Strategy

### Libraries

- **SimpleITK**: For image I/O and manipulation (efficient, supports 2D/3D, works with large images).
- **nnUNet**: For training and prediction (invoked as a subprocess, with portable data exchange).
- **Dask** or similar: For scalable, chunked, or parallel dataset operations (optional, for future scalability).

### Execution

- DAG is constructed from the user's program.
- Execution engine traverses the DAG, materializing results as needed, optimizing memory and computation.
- External tools (e.g., nnUNet) are invoked via well-defined interfaces.

---

## Execution Strategy: Sequential and Alternative Execution Models

### Primary Implementation: Sequential Execution
In the current implementation, each workflow (i.e., a single DAG representing a computation or analysis pipeline) is executed sequentially. This means that all operations within a workflow are executed in dependency order on a single process/thread, without parallelization at the level of individual operations.

For scalability, parallelization is introduced at the dataset level: when multiple independent workflows (e.g., the same pipeline applied to different images or dataset elements) need to be executed, Dask or a similar parallel scheduler is used to distribute these workflows across available compute resources. This approach avoids the complexity of fine-grained parallelism within a single workflow and leverages parallelism across the dataset dimension, which is often the most effective and scalable strategy for large-scale data processing.

- **Sequential execution:** Each workflow/DAG is executed in order, respecting dependencies, on a single worker.
- **Dataset-level parallelism:** Multiple workflows (e.g., per-dataset element) are scheduled in parallel using Dask, with each workflow running independently.

This sequential-per-workflow approach greatly simplifies memory management: since only one workflow is active at a time per worker, buffer allocation and reuse can be managed without concern for concurrent accesses or complex synchronization. This allows for straightforward implementation of static buffer reuse and preallocation strategies, as described in the memory planning documentation.

### Alternative Execution Models
VoxLogica-2 is designed to support multiple execution backends as alternatives to sequential execution:

- **Dynamic Scheduling with Storage-Based Memory**: Alternative execution engines that avoid buffer allocation in favor of persistent storage of intermediate results, enabling distributed and peer-to-peer execution patterns.
- **Actual DAG Node Execution**: Alternative backends that implement actual node execution (vs the current structure-only DAG computation) with caching and persistence capabilities.

These alternative execution models can coexist with the sequential execution approach, providing flexibility for different use cases and deployment scenarios.

---

## Open Questions

- How to represent and serialize datasets and workplans for distributed or remote execution?
- How to handle errors and partial failures in large, multi-step pipelines?
- What is the best way to expose extensibility for new data types and operations?

---

## Next Steps

- Discuss and refine these requirements and abstractions.
- Decide on core libraries and interfaces.
- Prototype the Dataset and Workplan abstractions.
- Define the interface for integrating external ML tools (e.g., nnUNet).

---

## Feature Implementation Status and Design Notes

### Implemented Features

- **Parsing and Translation to DAG**: Parsing of the VoxLogicA language and translation to a Directed Acyclic Graph (DAG) is implemented (`parser.py`, `reducer.py`).
- **Operation Type**: Each operation in the DAG is represented as an operator with argument dictionaries. Arguments use string numeric keys ("0", "1", etc.) mapping to operation IDs for extensibility while maintaining parser compatibility.

---

### Requirement: Content-Addressed DAG Node IDs

**Goal:**
Enable robust result tracking, efficient caching, reproducibility, and distributed execution by making every DAG node (operation) have a unique, content-addressed identifier.

**Design:**

- Each node's ID is the SHA-256 hash of its RFC-compliant, JSON-normalized record, recursively including the IDs of its argument nodes.
- Constants (numbers, strings, booleans) are hashed directly from their normalized JSON representation.
- This ensures that identical operations with identical inputs always have the same ID, enabling efficient caching and result reuse, and making results portable and shareable.

**JSON Normalization:**

- Use RFC 8785 (JCS) for canonicalization: canonical key ordering, consistent formatting, etc.
- Use a standard library, e.g. [`python-jcs`](https://pypi.org/project/python-jcs/) or [`canonicaljson`](https://pypi.org/project/canonicaljson/).
- See [rfc8785 implementations](https://github.com/cyberphone/json-canonicalization#implementations) for other languages.

**Implementation Note:**

- This scheme should be implemented in the `reducer` module, replacing integer-based IDs.
- The normalized, canonical JSON form of each operation node (including its arguments' IDs) is used to compute the SHA-256 hash.
- Tests should be written to ensure that equivalent operations always produce the same ID, and that changes in arguments or structure result in different IDs.

---

### Open Questions and Points for Discussion

1. **Node Record Structure:**

   - What fields must be included in the JSON record for each node? (e.g., operator name, argument IDs, parameters, metadata)
   - Should we include versioning or provenance information in the node record?

2. **Constants and Data Blobs:**

   - For large constants (e.g., image files), should the hash be computed on the file content, a reference, or a metadata record?
   - How do we handle non-JSON-serializable data (e.g., binary blobs, numpy arrays)?

3. **Error Handling and Partial Results:**

   - How should errors or partial failures be represented in the DAG/results?
   - Should error nodes have their own content-addressed IDs?

4. **Result Storage and Retrieval:**

   - Where and how are results for each node stored (local cache, remote store, etc.)?
   - Should the system support pluggable backends for result storage?

5. **Distributed/Remote Execution:**

   - How do we serialize/deserialize the DAG and datasets for distributed or remote execution?
   - Should we support partial materialization (only some nodes have results)?

6. **Extensibility:**

   - How do we allow new operator types or data types to be added without breaking the ID scheme?
   - Should operator definitions themselves be content-addressed?

7. **Security and Privacy:**
   - Are there privacy or security concerns with content-addressed IDs (e.g., leaking information via hashes)?

---

### Proposed Next Steps

- **Finalize the node record schema** (fields, required/optional, versioning).
- **Prototype the content-addressed ID computation** in `reducer.py` using a canonical JSON library.
- **Write tests** for ID stability and uniqueness.
- **Decide on result storage and retrieval mechanisms** (local/remote, pluggable?).
- **Document error handling and partial result semantics**.
- **Plan for extensibility** (operator/data type plugins, versioning).
- **Review security/privacy implications** of content-addressed IDs.

---

#### Questions for Review

- Should the node record include provenance/versioning fields?
- How should we handle large binary data (e.g., images) in the content-addressed scheme?
- Do you want to support pluggable result storage backends from the start?
- Is there a preferred canonical JSON library for Python in your environment?
- Should operator definitions themselves be content-addressed and versioned?

---

_This document is a starting point for discussion. Please review and suggest changes or additions before implementation begins._
