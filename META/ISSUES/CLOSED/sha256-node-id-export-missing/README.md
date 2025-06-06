# ISSUE: Missing SHA256 Node ID Field in DAG JSON Exports

## Status

**COMPLETED** ✅

## Background

The VoxLogicA system implements content-addressed DAG nodes using SHA256-based IDs (completed in ISSUE_SHA256_IDS). While the core reducer correctly generates and uses these SHA256 IDs for memoization and deduplication, the JSON export functionality in `WorkPlan.to_json()` does not include the SHA256 ID for each operation node.

## Problem

The exported `graph.json` (and possibly other outputs) does not include an `id` field for each node/operation. Instead, only the arguments of operations reference SHA256 hashes, but the nodes themselves do not expose their own hash as an `id`. This breaks downstream consumers (e.g., the D3.js visualization), which cannot match argument references to node objects, resulting in missing links/arrows in the graph.

### Current Export Format (Problematic)

```json
{
  "operations": [
    {
      "operator": 1.0,
      "arguments": {}
    },
    {
      "operator": 2.0,
      "arguments": {}
    },
    {
      "operator": "+",
      "arguments": {
        "0": "497bcaae20d2e0a846ec3cc7a9e4a8004784033f78e1d7e8aea1deb2181120d1",
        "1": "54a63a17ed8f1035bbc5a1285252650bfa566179ce91d0b3cc59e5cc335a3acc"
      }
    }
  ],
  "goals": [
    {
      "type": "print",
      "name": "sum",
      "operation_id": "ac29136665ea684835c7bc54621072ff668e632e283c69bf3975d7cc6feba022"
    }
  ]
}
```

### Required Export Format

```json
{
  "operations": [
    {
      "id": "497bcaae20d2e0a846ec3cc7a9e4a8004784033f78e1d7e8aea1deb2181120d1",
      "operator": 1.0,
      "arguments": {}
    },
    {
      "id": "54a63a17ed8f1035bbc5a1285252650bfa566179ce91d0b3cc59e5cc335a3acc",
      "operator": 2.0,
      "arguments": {}
    },
    {
      "id": "ac29136665ea684835c7bc54621072ff668e632e283c69bf3975d7cc6feba022",
      "operator": "+",
      "arguments": {
        "0": "497bcaae20d2e0a846ec3cc7a9e4a8004784033f78e1d7e8aea1deb2181120d1",
        "1": "54a63a17ed8f1035bbc5a1285252650bfa566179ce91d0b3cc59e5cc335a3acc"
      }
    }
  ],
  "goals": [
    {
      "type": "print",
      "name": "sum",
      "operation_id": "ac29136665ea684835c7bc54621072ff668e632e283c69bf3975d7cc6feba022"
    }
  ]
}
```

## Impact

- **Frontend Visualization**: The D3.js visualization cannot establish links between nodes because it cannot match argument SHA256 references to actual node objects without the `id` field.
- **Content-Addressed References**: External tools consuming the JSON cannot properly implement content-addressed lookups or caching.
- **API Consistency**: The JSON export does not fully reflect the content-addressed nature of the DAG nodes that exists internally.

## Root Cause Analysis

### Current Implementation

The issue is in the `WorkPlan.to_json()` method in `implementation/python/voxlogica/reducer.py` (lines ~245-272):

```python
def to_json(self) -> dict:
    """Return a JSON-serializable dict representing the work plan."""

    def op_to_dict(op):
        # Output numbers as JSON numbers, not strings
        if isinstance(op.operator, NumberOp):
            operator_value = op.operator.value
        else:
            operator_value = str(op.operator)
        return {
            "operator": operator_value,
            "arguments": op.arguments,
        }
    # ... missing "id" field
```

The method only exports the `operator` and `arguments` fields but omits the SHA256 `id` that corresponds to each operation.

### Architecture Issue

The `WorkPlan` class maintains operations as a `List[Operation]` but stores the mapping from operations to their IDs separately in `_operation_ids: Optional[Dict[Operation, OperationId]]`. The `to_json()` method only iterates over the operations list without accessing their corresponding IDs.

## Solution Implemented ✅

### Modification Made

Updated the `WorkPlan.to_json()` method to include the SHA256 ID for each operation:

1. **Access Operation IDs**: Use the `_get_operations_with_ids()` method (which already exists for DOT export) to get operation-ID pairs.
2. **Include ID Field**: Add the `id` field to each operation dictionary in the JSON export.
3. **Maintain Consistency**: Ensure the same ID scheme is used across DOT, JSON, and any other export formats.

### Implementation Details

File: `implementation/python/voxlogica/reducer.py`
Method: `WorkPlan.to_json()` (approximately lines 245-272)

### Code Change Applied

```python
def to_json(self) -> dict:
    """Return a JSON-serializable dict representing the work plan."""

    def op_to_dict(op_id, op):
        # Output numbers as JSON numbers, not strings
        if isinstance(op.operator, NumberOp):
            operator_value = op.operator.value
        else:
            operator_value = str(op.operator)
        return {
            "id": op_id,  # Add the SHA256 ID field
            "operator": operator_value,
            "arguments": op.arguments,
        }

    def goal_to_dict(goal):
        if isinstance(goal, GoalSave):
            return {
                "type": "save",
                "name": goal.name,
                "operation_id": goal.operation_id,
            }
        elif isinstance(goal, GoalPrint):
            return {
                "type": "print",
                "name": goal.name,
                "operation_id": goal.operation_id,
            }
        else:
            return {"type": "unknown"}

    # Use _get_operations_with_ids() to get both operation and ID
    operations_with_ids = self._get_operations_with_ids()

    return {
        "operations": [op_to_dict(op_id, op) for op_id, op in operations_with_ids],
        "goals": [goal_to_dict(goal) for goal in self.goals],
    }
```

## Acceptance Criteria ✅

- [x] Every node in the JSON export has an explicit `id` field containing its SHA256 hash
- [x] The D3.js visualization can successfully match argument references to node objects
- [x] All existing functionality remains intact (no breaking changes)
- [x] Export format is consistent across DOT and JSON outputs
- [x] Tests are updated to verify the presence of `id` fields in JSON exports

## Testing ✅

### Tests Implemented

Created comprehensive test suite in `tests/test_sha256_json_export.py`:

1. **test_json_export_includes_sha256_ids**: Verifies that each operation has an `id` field with proper SHA256 format (64 hex characters)
2. **test_json_export_id_matches_arguments**: Ensures argument references can be matched to actual operation nodes by their IDs
3. **test_json_export_goal_references_valid_id**: Confirms that goals reference valid operation IDs
4. **test_json_export_deterministic_ids**: Verifies that same program produces same IDs across multiple runs
5. **test_json_export_consistent_with_internal_ids**: Ensures JSON export IDs match internal operation ID tracking

### Test Results

All 5 new tests pass, plus all existing related tests continue to pass:

- `tests/test_sha256_json_export.py`: 5/5 tests pass ✅
- `tests/test_dag_dict_args.py`: 4/4 tests pass ✅
- `tests/test_sha256_memoization.py`: 6/6 tests pass ✅

### Verification

Manual verification with test program:

```bash
echo "let a = 1\nlet b = 2\nlet c = a + b\nprint \"sum\" c" > /tmp/test.imgql
./voxlogica run /tmp/test.imgql --save-task-graph-as-json /tmp/test.json
```

Output now correctly includes `id` field for each operation:

```json
{
  "operations": [
    {
      "id": "497bcaae20d2e0a846ec3cc7a9e4a8004784033f78e1d7e8aea1deb2181120d1",
      "operator": 1.0,
      "arguments": {}
    }
    // ... other operations with id fields
  ]
}
```

## References

- **Related Issues**: ISSUE_SHA256_IDS (completed) - implemented the underlying SHA256 ID system
- **Documentation**: `doc/dev/SEMANTICS.md` - Content-Addressed DAG Node IDs section
- **Frontend Code**: `implementation/python/voxlogica/static/index.html` - D3.js visualization expecting `id` field
- **Backend Code**: `implementation/python/voxlogica/reducer.py` - `WorkPlan.to_json()` method

## Design Context

This issue affects the content-addressed DAG semantics which are foundational to VoxLogicA's:

- **Robust result tracking**: Enables persistent caching across sessions
- **Efficient memoization**: Avoids recomputation of identical operations
- **Reproducibility**: Same operations always produce same IDs
- **Distributed execution**: Content-addressed IDs are portable and shareable

The missing `id` field broke the content-addressed promise in external-facing exports, making it impossible for downstream tools to leverage the content-addressed properties of the DAG. This has now been resolved.

## Follow-up Actions

- Frontend D3.js visualization should now be able to correctly render node connections
- Other consumers of the JSON export can now implement content-addressed caching and lookups
- The export format is now consistent between DOT and JSON outputs
