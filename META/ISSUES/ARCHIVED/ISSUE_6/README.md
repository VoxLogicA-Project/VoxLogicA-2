# Issue 6: Test: Ensure identical DAGs for same program in both ports (with JSON normalization)

## Problem

We need to create tests that show how the same program produces the same DAG in both the Python and F# ports.

## Task

- Generate 5 test cases (imgql programs) that are valid and representative.
- For each, run both ports and obtain the resulting DAG as JSON.
- Normalize the resulting JSON using a standard normalization procedure (e.g., sort keys, remove whitespace, canonicalize numbers/strings).
- Compare the normalized JSON outputs to ensure they are identical.
- Document the normalization procedure and any edge cases.

## Acceptance Criteria

- At least 5 test cases are generated and included in the test suite.
- The test suite runs both ports, normalizes the output, and compares them.
- Any differences are reported with clear diagnostics.
- Documentation is updated to describe the normalization and comparison process.

## Notes

- Use a standard for JSON normalization (e.g., RFC 8785 or Python's json.dumps(..., sort_keys=True, separators=(",", ":"))).
- Ensure the test is robust to irrelevant differences (e.g., whitespace, key order).
- This will help guarantee true CLI/API parity for users.

- GitHub Issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/6
