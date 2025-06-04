"""
Tests for the run feature
"""

import pytest
import tempfile
import os
import json
import sys

# Add the implementation path to sys.path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "../../implementation/python")
)

from voxlogica.features import FeatureRegistry


def test_run_feature_exists():
    """Test that the run feature is registered"""
    feature = FeatureRegistry.get_feature("run")
    assert feature is not None
    assert feature.name == "run"
    assert feature.description == "Run a VoxLogicA program with various output options"


def test_run_feature_basic_program():
    """Test running a basic VoxLogicA program"""
    feature = FeatureRegistry.get_feature("run")

    program = """let a = 1
let b = 2
let c = a + b
print "sum" c"""

    result = feature.handler(program=program)

    assert hasattr(result, "success")
    assert result.success is True
    assert hasattr(result, "data")
    assert result.data is not None

    data = result.data
    assert "operations" in data
    assert "goals" in data
    assert "task_graph" in data
    assert "syntax" in data

    assert data["operations"] == 3  # a=1, b=2, c=a+b
    assert data["goals"] == 1  # print statement


def test_run_feature_with_json_export():
    """Test running a program with JSON export"""
    feature = FeatureRegistry.get_feature("run")

    program = """let a = 1
let b = 2
let c = a + b
print "sum" c"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json_file = f.name

    try:
        result = feature.handler(program=program, save_task_graph_as_json=json_file)

        assert result.success is True
        assert "saved_files" in result.data
        assert len(result.data["saved_files"]) == 1
        assert json_file in result.data["saved_files"][0]

        # Verify the JSON file was created and has valid content
        assert os.path.exists(json_file)
        with open(json_file, "r") as f:
            json_data = json.load(f)

        assert "operations" in json_data
        assert "goals" in json_data
        assert len(json_data["operations"]) == 3
        assert len(json_data["goals"]) == 1

    finally:
        if os.path.exists(json_file):
            os.unlink(json_file)


def test_run_feature_with_dot_export():
    """Test running a program with DOT export"""
    feature = FeatureRegistry.get_feature("run")

    program = """let a = 1
let b = 2
let c = a + b
print "sum" c"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        dot_file = f.name

    try:
        result = feature.handler(program=program, save_task_graph=dot_file)

        assert result.success is True
        assert "saved_files" in result.data
        assert len(result.data["saved_files"]) == 1
        assert dot_file in result.data["saved_files"][0]

        # Verify the DOT file was created and has valid content
        assert os.path.exists(dot_file)
        with open(dot_file, "r") as f:
            dot_content = f.read()

        # DOT files should contain digraph declaration
        assert "digraph" in dot_content or "graph" in dot_content

    finally:
        if os.path.exists(dot_file):
            os.unlink(dot_file)


def test_run_feature_with_multiple_exports():
    """Test running a program with multiple export options"""
    feature = FeatureRegistry.get_feature("run")

    program = """let a = 1
let b = 2
let c = a + b
print "sum" c"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json_file = f.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        dot_file = f.name

    try:
        result = feature.handler(
            program=program,
            save_task_graph_as_json=json_file,
            save_task_graph_as_dot=dot_file,
        )

        assert result.success is True
        assert "saved_files" in result.data
        assert len(result.data["saved_files"]) == 2

        # Both files should be mentioned in saved_files
        saved_files_str = " ".join(result.data["saved_files"])
        assert json_file in saved_files_str
        assert dot_file in saved_files_str

        # Both files should exist
        assert os.path.exists(json_file)
        assert os.path.exists(dot_file)

    finally:
        for file in [json_file, dot_file]:
            if os.path.exists(file):
                os.unlink(file)


def test_run_feature_invalid_program():
    """Test running an invalid VoxLogicA program"""
    feature = FeatureRegistry.get_feature("run")

    # Invalid syntax
    program = "invalid syntax here"

    result = feature.handler(program=program)

    assert hasattr(result, "success")
    assert result.success is False
    assert hasattr(result, "error")
    assert result.error is not None
