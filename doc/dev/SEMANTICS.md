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
- **Parsing and Translation to DAG**: The system already implements parsing of the VoxLogicA language and translation to a Directed Acyclic Graph (DAG) representation. See `implementation/python/voxlogica/parser.py` and `implementation/python/voxlogica/reducer.py` for details.
- **Operation Type**: In the current implementation (see `reducer.py`), each operation in the DAG is represented as an operator with argument IDs. Currently, these IDs are integers.

### Requirement: Content-Addressed DAG Node IDs
To improve result tracking, avoid recomputation, and ensure reproducibility, every node (operation) in the DAG should have a unique identifier computed as follows:
- The ID of each node is the SHA-256 hash of its RFC-compliant, JSON-normalized record, computed recursively (i.e., including the IDs of its argument nodes).
- Basic constants (e.g., numbers, strings, booleans) are hashed directly from their normalized JSON representation.
- This approach ensures that identical operations with identical inputs always have the same ID, enabling efficient caching and result reuse.

#### JSON Normalization
- JSON normalization is required to ensure that logically equivalent records always produce the same hash. This involves:
  - Canonical key ordering
  - Consistent formatting (e.g., whitespace, number representations)
  - Use of a de-facto standard normalizer, such as [rfc8785](https://datatracker.ietf.org/doc/html/rfc8785) (JSON Canonicalization Scheme, JCS)
- Libraries for JSON normalization include:
  - Python: [`python-jcs`](https://pypi.org/project/python-jcs/), [`canonicaljson`](https://pypi.org/project/canonicaljson/)
  - Other languages: see [rfc8785 implementations](https://github.com/cyberphone/json-canonicalization#implementations)

#### Implementation Note
- This content-addressed ID scheme can be implemented in the `reducer` module, replacing the current integer-based IDs.
- The system should use the normalized, canonical JSON form of each operation node (including its arguments' IDs) to compute the SHA-256 hash.

---

*This document is a starting point for discussion. Please review and suggest changes or additions before implementation begins.*
