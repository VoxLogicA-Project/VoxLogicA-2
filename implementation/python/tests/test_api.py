"""Tests for VoxLogicA API endpoints."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from voxlogica.main import api_app

# Sample VoxLogicA program for testing
SAMPLE_PROGRAM = """
// A simple VoxLogicA program for testing
let image = ImageId("test_image");
let threshold = 128;
let binary = image > threshold;

// Save the result
binary
"""

@pytest.fixture
def test_program_file(tmp_path: Path) -> str:
    """Create a temporary test program file and return its path."""
    file_path = tmp_path / "test.vox"
    file_path.write_text(SAMPLE_PROGRAM)
    return str(file_path)


def test_version_endpoint(api_client: TestClient) -> None:
    """Test the /version endpoint."""
    response = api_client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert isinstance(data["version"], str)


def test_program_endpoint(api_client: TestClient, test_program_file: str) -> None:
    """Test the /program endpoint with a sample program."""
    with open(test_program_file, "r") as f:
        program_content = f.read()
    
    response = api_client.post(
        "/program",
        json={"program": program_content}
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Check the response structure
    assert "operations" in data
    assert "goals" in data
    assert "task_graph" in data
    assert "dot_graph" in data
    assert "syntax" in data
    
    # Check that we got some reasonable values back
    assert isinstance(data["operations"], int)
    assert isinstance(data["goals"], int)
    assert isinstance(data["task_graph"], str)
    assert isinstance(data["dot_graph"], str)
    assert isinstance(data["syntax"], str)


def test_save_task_graph_endpoint(api_client: TestClient, test_program_file: str, tmp_path: Path) -> None:
    """Test the /save-task-graph endpoint."""
    with open(test_program_file, "r") as f:
        program_content = f.read()
    
    output_file = tmp_path / "output.dot"
    
    response = api_client.post(
        "/save-task-graph",
        json={
            "program": program_content,
            "filename": str(output_file)
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert str(output_file) in data["message"]
    
    # Verify the file was created and has content
    assert output_file.exists()
    assert output_file.stat().st_size > 0


def test_invalid_program(api_client: TestClient) -> None:
    """Test error handling for invalid programs."""
    response = api_client.post(
        "/program",
        json={"program": "invalid syntax"}
    )
    
    # Should return a 400 or 422 error for invalid syntax
    assert response.status_code in (400, 422)
    data = response.json()
    assert "detail" in data or "error" in data.get("detail", {})
