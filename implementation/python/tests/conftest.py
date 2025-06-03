"""pytest configuration and fixtures for VoxLogicA tests"""
import os
import sys
import time
import subprocess
import signal
from pathlib import Path
from typing import Generator, Optional

import pytest
import requests
from fastapi.testclient import TestClient

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from voxlogica.main import api_app


@pytest.fixture(scope="session")
def test_server() -> Generator[None, None, None]:
    """Start the test server as a fixture."""
    # This is a test client that doesn't actually start a server
    # For actual server tests, we'll use a different approach
    yield


@pytest.fixture(scope="function")
def api_client() -> TestClient:
    """Create a test client for the API."""
    return TestClient(api_app)


class TestServer:
    """Context manager for managing a test server process."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.base_url = f"http://{self.host}:{self.port}"
    
    def start(self) -> None:
        """Start the test server."""
        if self.process is not None:
            return
            
        # Start the server in a subprocess
        self.process = subprocess.Popen(
            [sys.executable, "-m", "voxlogica.main", "serve", "--host", self.host, "--port", str(self.port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy()
        )
        
        # Wait for the server to start
        max_attempts = 10
        for _ in range(max_attempts):
            try:
                response = requests.get(f"{self.base_url}/version", timeout=1)
                if response.status_code == 200:
                    return
            except (requests.RequestException, ConnectionError):
                time.sleep(0.5)
        
        self.stop()
        raise RuntimeError("Failed to start test server")
    
    def stop(self) -> None:
        """Stop the test server."""
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


@pytest.fixture(scope="function")
def test_server_process() -> Generator[TestServer, None, None]:
    """Fixture that provides a running test server."""
    server = TestServer()
    try:
        server.start()
        yield server
    finally:
        server.stop()
