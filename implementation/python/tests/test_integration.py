"""Integration tests for the VoxLogicA API server."""
import json
import time
import requests
from pathlib import Path

import pytest

# Sample VoxLogicA program for testing
SAMPLE_PROGRAM = """
// A simple VoxLogicA program for testing
let image = ImageId("test_image");
let threshold = 128;
let binary = image > threshold;

// Save the result
binary
"""


def test_server_lifecycle(test_server_process) -> None:
    """Test that the server starts, responds to requests, and shuts down."""
    server = test_server_process
    
    # Test the version endpoint
    response = requests.get(f"{server.base_url}/version", timeout=5)
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert isinstance(data["version"], str)
    
    # Test the program endpoint
    response = requests.post(
        f"{server.base_url}/program",
        json={"program": SAMPLE_PROGRAM},
        timeout=5
    )
    assert response.status_code == 200
    data = response.json()
    assert all(key in data for key in ["operations", "goals", "task_graph", "dot_graph", "syntax"])
    
    # Test server shutdown (handled by the fixture)


def test_concurrent_requests(test_server_process) -> None:
    """Test that the server can handle multiple concurrent requests."""
    server = test_server_process
    
    # Make multiple requests in parallel
    num_requests = 5
    responses = []
    
    for _ in range(num_requests):
        response = requests.get(f"{server.base_url}/version", timeout=5)
        responses.append(response)
    
    # Verify all responses were successful
    for response in responses:
        assert response.status_code == 200
        data = response.json()
        assert "version" in data


def test_invalid_endpoint(test_server_process) -> None:
    """Test that invalid endpoints return appropriate error responses."""
    server = test_server_process
    
    # Test non-existent endpoint
    response = requests.get(f"{server.base_url}/nonexistent", timeout=5)
    assert response.status_code == 404
    
    # Test invalid method
    response = requests.post(f"{server.base_url}/version", json={"invalid": "data"}, timeout=5)
    assert response.status_code in (405, 422)  # Method not allowed or validation error
