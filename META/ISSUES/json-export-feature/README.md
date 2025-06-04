# Issue 7: Feature: Export task graph as JSON (CLI option)

## Status

**COMPLETED** - JSON export feature has been implemented and is available via both CLI and API.

## Problem

No CLI option to export the task graph as JSON in either the Python or F# port. This is needed for robust, automated equivalence testing and for downstream tools.

## Task

- Add a CLI option to both the Python and F# ports to export the task graph as JSON (e.g., --save-task-graph-as-json <file>).
- The exported JSON should be a faithful representation of the task graph, but normalization (e.g., key order, whitespace) will be handled in the test script, not in the CLI export.
- Ensure the output is robust and documented.
- Update documentation and CLI help to describe the new option.

## Acceptance Criteria

- Both ports support a CLI option to export the task graph as JSON.
- The output is consistent across runs and platforms (modulo normalization, which is handled in the test script).
- Documentation and CLI help are updated.
- The new option is used in the DAG equivalence test.

## Implementation

**Python Port (COMPLETED):**

- Added `--save-task-graph-as-json` CLI option to the `run` command
- Implemented `handle_save_task_graph_json` feature handler in `features.py`
- Added JSON export functionality using the existing `WorkPlan.to_json()` method
- Feature is available via both CLI and API endpoints

**F# Port (PENDING):**

- Not yet implemented

## Usage

**CLI:**

```bash
./voxlogica run program.imgql --save-task-graph-as-json output.json
```

**API:**

```bash
curl -X POST http://localhost:8000/api/v1/save-task-graph-json \
  -H "Content-Type: application/json" \
  -d '{"program": "let a = 1\nprint a", "filename": "output.json"}'
```

## Notes

- Normalization is not required in the CLI export; it will be performed in the test script for robust comparison and reproducibility.
- This will enable true CLI/API parity and support for automated testing and integration.

- Related to: #6
- GitHub Issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/7
