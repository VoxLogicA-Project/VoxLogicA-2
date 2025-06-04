# ISSUE: Refactor DAG Operator Arguments to Named Dicts in Reducer

## Background
Currently, each operator in the DAG (workplan) is constructed with a list of ordered arguments. To improve clarity, extensibility, and future-proofing, the DAG should be refactored so that each operator accepts a Python dict of named arguments. For now, to maintain compatibility with the parser and language (which are order-based), the reducer should produce argument dicts with numeric string keys (e.g., "0", "1", etc.) corresponding to the original argument order.

**Note:** Do not change the parser or the language at this stage. This change is only in the reducer and downstream DAG representation.

## Tasks
- Refactor the reducer module so that each operator node in the DAG uses a dict of named arguments instead of a list.
  - The argument names should be numeric strings ("0", "1", ...) corresponding to their order in the parsed language.
- Update all relevant code in the reducer and downstream consumers to use the new argument dict structure.
- Write or update tests to ensure correct behavior and backward compatibility.
- Update project documentation to reflect the new argument structure in the DAG (workplan) representation.

## Acceptance Criteria
- All operator nodes in the DAG use a dict of named arguments (numeric keys for now).
- No changes to the parser or language are required.
- Tests are present and passing.
- Documentation is updated accordingly.
