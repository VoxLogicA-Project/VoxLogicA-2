# ISSUE: Refactor DAG Operator Arguments to Named Dicts in Reducer

## Status

**COMPLETED** - DAG operations now use dict arguments with string numeric keys as specified.

## Background

Currently, each operator in the DAG (workplan) is constructed with a list of ordered arguments. To improve clarity, extensibility, and future-proofing, the DAG should be refactored so that each operator accepts a Python dict of named arguments. For now, to maintain compatibility with the parser and language (which are order-based), the reducer should produce argument dicts with numeric string keys (e.g., "0", "1", etc.) corresponding to the original argument order.

**Note:** Do not change the parser or the language at this stage. This change is only in the reducer and downstream DAG representation.

## Tasks

- ✅ Refactor the reducer module so that each operator node in the DAG uses a dict of named arguments instead of a list.
- ✅ The argument names when coming from the parser should be json numbers (0, 1, ...) corresponding to their order in the parsed language.
- ✅ Update all relevant code in the reducer and downstream consumers to use the new argument dict structure.
- ✅ Write or update tests to ensure correct behavior and backward compatibility.
- ✅ Update project documentation to reflect the new argument structure in the DAG (workplan) representation.

## Implementation Summary

**Changes Made:**

- Updated `Arguments` type from `List[OperationId]` to `Dict[str, OperationId]` in `reducer.py`
- Modified `Operation` class to use dict-based arguments with string numeric keys
- Updated all argument processing in `reduce_expression()` to convert lists to dicts with string keys
- Fixed `Operations` class memoization to handle dict arguments correctly
- Updated `WorkPlan` methods (`to_dot()`, `to_json()`, `to_program()`) to iterate over dict values
- Modified `__str__` methods to display arguments correctly

**Test Coverage:**

- Created comprehensive test suite in `tests/test_dag_dict_args.py`
- Tests verify dict structure, string numeric keys, and JSON serialization
- All existing tests continue to pass, ensuring backwards compatibility

**Documentation Updates:**

- Updated `doc/reducer-initial-analysis.md` to reflect dict-based arguments
- Updated `doc/dev/SEMANTICS.md` to describe new operation structure

## Acceptance Criteria

- ✅ All operator nodes in the DAG use a dict of named arguments (numeric keys for now).
- ✅ No changes to the parser or language are required.
- ✅ Tests are present and passing. In particular at least one test checking the json numbers for argument names.
- ✅ Documentation is updated accordingly.
