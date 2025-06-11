# Issue 2: Redundant or Unnecessary Checks in Argument Resolution

## Description

There is a redundant fallback branch in the `_resolve_arguments` method in `voxlogica/execution.py`:

```python
if arg_value in dep_results_map:
    resolved[arg_name] = dep_results_map[arg_value]
else:
    # Direct value (should not happen with content-addressed IDs)
    resolved[arg_name] = arg_value
```

This fallback should be unreachable in the new node-based architecture. If it is reached, it may indicate a logic error elsewhere. The code should raise an explicit error instead of silently passing through, to avoid masking bugs.

## Steps to Reproduce
- Run any program that triggers argument resolution for nodes.
- If the fallback is hit, it will silently pass through an unexpected value.

## Expected Behavior
- The fallback should raise an error if reached, making debugging easier and preventing silent failures.

## Related Files
- `implementation/python/voxlogica/execution.py`

## Linked Tests
- (Link to any test that would exercise this code path, if available)

---

# Issue 3: Inconsistent Serialization in JSON Converter

## Description

In `voxlogica/converters/json_converter.py`, both a `WorkPlanJSONEncoder` class and a local `unwrap` function are defined. The encoder uses its own `_unwrap` method, but the local `unwrap` is used in `to_json`. This could lead to inconsistent serialization and confusion.

## Steps to Reproduce
- Use the JSON converter in different contexts.
- Observe that different unwrapping logic may be used depending on the code path.

## Expected Behavior
- There should be a single, consistent unwrapping/serialization logic for nodes and workplans.

## Related Files
- `implementation/python/voxlogica/converters/json_converter.py`

## Linked Tests
- (Link to any test that would exercise this code path, if available)

---

# Issue 4.1: Fallback for Missing Node Type in Visualization

## Description

In `voxlogica/static/index.html`, the D3 visualization expects each node to have a `type` field. If this field is missing, the visualization may break. There should be a fallback to handle nodes without a `type` field gracefully.

## Steps to Reproduce
- Visualize a task graph JSON that lacks the `type` field for some nodes.

## Expected Behavior
- The visualization should not break and should assign a default type (e.g., "operation") if missing.

## Related Files
- `implementation/python/voxlogica/static/index.html`

## Linked Tests
- (Link to any test that would exercise this code path, if available)
