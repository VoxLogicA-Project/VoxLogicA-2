from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxlogica.features import FeatureRegistry


@pytest.mark.integration
def test_version_feature_contract():
    feature = FeatureRegistry.get_feature("version")
    assert feature is not None

    result = feature.handler()
    assert result.success is True
    assert isinstance(result.data, dict)
    assert isinstance(result.data.get("version"), str)
    assert "." in result.data["version"]


@pytest.mark.integration
def test_run_feature_basic_program_and_exports(tmp_path: Path):
    feature = FeatureRegistry.get_feature("run")
    assert feature is not None

    program = """let a = 1
let b = 2
let c = a + b
print "sum" c"""

    json_path = tmp_path / "graph.json"
    dot_path = tmp_path / "graph.dot"

    result = feature.handler(
        program=program,
        save_task_graph_as_json=str(json_path),
        save_task_graph_as_dot=str(dot_path),
        execute=False,
    )

    assert result.success is True
    assert isinstance(result.data, dict)
    assert result.data["operations"] >= 1
    assert result.data["goals"] == 1

    saved = result.data.get("saved_files", {})
    assert str(json_path) in saved
    assert str(dot_path) in saved

    graph_json = saved[str(json_path)]
    assert isinstance(graph_json, dict)
    assert "nodes" in graph_json
    assert "goals" in graph_json

    graph_dot = saved[str(dot_path)]
    assert isinstance(graph_dot, str)
    assert "digraph" in graph_dot

    # Keep compatibility check that output is API-serializable.
    json.dumps(result.data)


@pytest.mark.integration
def test_run_feature_rejects_invalid_program():
    feature = FeatureRegistry.get_feature("run")
    assert feature is not None

    result = feature.handler(program="invalid syntax here", execute=False)
    assert result.success is False
    assert isinstance(result.error, str)
