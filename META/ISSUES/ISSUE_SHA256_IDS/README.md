# ISSUE: Implement Content-Addressed SHA256-Based DAG Node IDs with JSON Normalization

## Background
The current implementation of the DAG (workplan) in VoxLogicA uses integer-based IDs for operation nodes. To improve result tracking, avoid recomputation, and ensure reproducibility, every node (operation) in the DAG should have a unique identifier computed as the SHA-256 hash of its RFC-compliant, JSON-normalized record, recursively (including the IDs of its argument nodes). See `doc/dev/SEMANTICS.md` for detailed requirements and references.

## Tasks
- Implement content-addressed IDs for all DAG nodes in the `reducer` module:
  - Use SHA-256 of the RFC 8785 (JCS) canonical JSON representation of each node, recursively including argument IDs.
  - For constants (numbers, strings, booleans), hash their normalized JSON representation directly.
  - Use a standard Python library for JSON canonicalization (e.g., `python-jcs` or `canonicaljson`).
- Write tests to ensure:
  - Equivalent operations always produce the same ID.
  - Changes in arguments or structure result in different IDs.
- Update project documentation to reflect the new ID scheme and its rationale.

## References
- `doc/dev/SEMANTICS.md` (see section on Content-Addressed DAG Node IDs)
- [RFC 8785 - JSON Canonicalization Scheme (JCS)](https://datatracker.ietf.org/doc/html/rfc8785)
- [python-jcs](https://pypi.org/project/python-jcs/), [canonicaljson](https://pypi.org/project/canonicaljson/)

## Acceptance Criteria
- All DAG nodes use content-addressed SHA256-based IDs as described.
- Tests for ID stability and uniqueness are present and passing.
- Documentation is updated accordingly.
