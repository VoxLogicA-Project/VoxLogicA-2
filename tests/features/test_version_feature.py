"""
Tests for the version feature
"""

import pytest
import sys
import os

description = """Tests the 'version' feature of the VoxLogicA API, ensuring the version feature is registered, the handler works, and the version string is valid."""

# Add the implementation path to sys.path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "../../implementation/python")
)

from voxlogica.features import FeatureRegistry


def test_version_feature_exists():
    """Test that the version feature is registered"""
    feature = FeatureRegistry.get_feature("version")
    assert feature is not None
    assert feature.name == "version"
    assert feature.description == "Get the VoxLogicA version"


def test_version_feature_handler():
    """Test that the version feature handler works"""
    feature = FeatureRegistry.get_feature("version")
    result = feature.handler()

    assert hasattr(result, "success")
    assert result.success is True
    assert hasattr(result, "data")
    assert result.data is not None
    assert "version" in result.data
    assert isinstance(result.data["version"], str)


def test_version_feature_returns_valid_version():
    """Test that the version feature returns a valid version string"""
    feature = FeatureRegistry.get_feature("version")
    result = feature.handler()

    version = result.data["version"]
    # Should be in format like "0.1.0" or "0.2.0-alpha"
    assert len(version) > 0
    assert "." in version  # Should have at least one dot for major.minor


if __name__ == "__main__":
    print(f"\nTest Description: {description}\n")
    pytest.main([__file__])
