#!/usr/bin/env python3
import jcs
import json

# Python JSON
py_json = {
    "operations": [
        {"operator": 3.14, "arguments": []},
        {"operator": 2.0, "arguments": []},
        {"operator": "*", "arguments": [1, 1]},
        {"operator": "*", "arguments": [0, 2]},
    ],
    "goals": [{"type": "print", "name": "area", "operation_id": 3}],
}

# F# JSON
fs_json = {
    "operations": [
        {"operator": 2, "arguments": []},
        {"operator": 3.14, "arguments": []},
        {"operator": "*", "arguments": [0, 0]},
        {"operator": "*", "arguments": [1, 2]},
    ],
    "goals": [{"type": "print", "name": "area", "operation_id": 3}],
}

print("Python JCS:", jcs.canonicalize(py_json))
print("F# JCS:    ", jcs.canonicalize(fs_json))
print("Equal:", jcs.canonicalize(py_json) == jcs.canonicalize(fs_json))
