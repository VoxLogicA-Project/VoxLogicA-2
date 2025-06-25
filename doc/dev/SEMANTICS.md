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

## Execution Strategy: Distributed Execution with Dask

### Primary Implementation: Dask-Based Distributed Execution
The current implementation uses **Dask delayed graphs** for distributed execution of VoxLogicA workplans. Each workflow (DAG) is compiled into a Dask delayed computation graph that enables:

- **Parallel execution**: Operations are executed in parallel when dependencies allow, leveraging available compute resources.
- **Distributed scheduling**: Dask can distribute computation across multiple workers or machines.
- **Lazy evaluation**: Operations are not executed until results are actually needed.
- **Automatic dependency resolution**: Dask handles the complex scheduling of operations based on their dependencies.

The execution engine separates operations into two categories:
- **Pure operations**: Mathematical and data processing operations that can be parallelized and cached
- **Side-effect operations**: I/O operations like `print`, `save`, etc. that are executed separately after pure computations

This distributed approach provides:
- **Scalability**: Can leverage multiple cores and distributed computing resources
- **Memory efficiency**: Only required data is loaded when needed
- **Fault tolerance**: Dask provides built-in retry and error handling capabilities
- **Content-addressed caching**: Results are cached using SHA-256 hashes for efficient reuse

### Storage-Based Execution Model
The execution engine integrates with a persistent storage backend that:

- **Content-addressed storage**: All intermediate and final results are stored using content-addressed identifiers (SHA-256 hashes)
- **Persistent caching**: Results persist across execution sessions, enabling efficient re-runs
- **Distributed storage**: Storage backend can be distributed across multiple machines
- **Custom serialization**: Supports custom serializers for different data types and file formats

### Execution Flow
1. **Compilation**: VoxLogicA programs are parsed into DAGs and compiled to Dask delayed graphs
2. **Pure computation**: Mathematical operations are executed in parallel using Dask
3. **Side-effect execution**: I/O operations are executed after computations complete
4. **Result storage**: All results are persisted in the content-addressed storage backend

This architecture enables scalable, distributed execution while maintaining reproducibility and efficient caching of intermediate results.

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
- **Closure-Based For-Loops**: Distributed for-loop execution using proper closures with environment capture and distributed execution support.

---

### For-Loop Implementation: Closure-Based Distributed Execution

**Goal:**
Enable robust for-loop execution in distributed environments with proper variable scoping and environment management.

**Architecture:**

VoxLogicA for-loops use a closure-based approach that captures:
1. **Variable binding**: The loop variable name (e.g., 'i', 'img')
2. **Expression AST**: The loop body as a parsed Expression object
3. **Environment**: The lexical environment at closure creation time
4. **Workplan context**: Reference to the workplan for dependency resolution

**Closure Execution Model:**

```python
# For-loop: for i in range(0,3) do +(i, 1)
# Creates closure: ClosureValue(variable='i', expression=AST(+(i,1)), env=..., workplan=...)

def closure_execution(value):
    # 1. Bind loop variable to current value
    new_env = captured_env.bind('i', value) 
    
    # 2. Reduce expression in new environment
    result_id = reduce_expression(new_env, workplan, expression)
    
    # 3. Execute operation directly with proper argument mapping
    operation = workplan.nodes[result_id]
    primitive = load_primitive(operation.operator)
    resolved_args = resolve_and_map_arguments(operation.arguments)
    return primitive(**resolved_args)
```

**Key Features:**

1. **Environment Preservation**: Closures capture the lexical environment, enabling proper variable scoping in nested contexts
2. **Direct Operation Execution**: Bypasses full execution engine for efficiency while maintaining correctness
3. **Argument Mapping**: Converts numeric argument keys ('0', '1') to semantic names ('left', 'right') for primitive compatibility
4. **Distributed Execution**: Works correctly in Dask workers with proper serialization handling
5. **Graceful Fallback**: Returns original values when closure execution encounters unresolvable dependencies

**Storage Integration:**

- **Serializable closures**: Handled via special case in `_compute_node_id()` using expression syntax for hashing
- **Non-serializable results**: Stored in memory cache rather than persistent SQLite storage
- **Cross-worker communication**: Closures work correctly across distributed Dask workers

**Implementation Details:**

```python
@dataclass  
class ClosureValue:
    variable: str          # Parameter name (e.g., 'i')
    expression: Expression # AST expression (not string)
    environment: Environment # Captured environment
    workplan: WorkPlan    # Reference for context
```

**Nested For-Loop Support:**

```voxlogica
let dataset = for i in range(0,10) do BinaryThreshold(img, 100+i, 99999, 1, 0)
let processed = for img in dataset do MinimumMaximumImageFilter(img)
```

Each for-loop creates its own closure with the appropriate environment capture, enabling complex nested operations with proper variable scoping.

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
