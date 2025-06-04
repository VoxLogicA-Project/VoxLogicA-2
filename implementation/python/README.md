# VoxLogicA 2 - Python Implementation

This is the Python implementation of VoxLogicA 2, a spatial model checker and image analysis tool.

## Installation

### From Source

Clone the repository and install the Python package:

```bash
# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install the package in development mode
pip install -e .
```

## Usage

### CLI

VoxLogicA can be used as a command-line tool:

```bash
# Show help
voxlogica --help

# Show version
voxlogica version

# Run a VoxLogicA program
voxlogica run path/to/program.imgql

# Run with debug output
voxlogica run path/to/program.imgql --debug

# Save the task graph as a DOT file
voxlogica run path/to/program.imgql --save-task-graph graph.dot

# Save the task graph as JSON
voxlogica run path/to/program.imgql --save-task-graph-as-json graph.json

# Save multiple formats
voxlogica run path/to/program.imgql --save-task-graph graph.dot --save-task-graph-as-json graph.json
```

### API Server

VoxLogicA can also be run as an API server:

```bash
# Start the API server
voxlogica serve

# Start with custom host and port
voxlogica serve --host 0.0.0.0 --port 8080
```

Once the server is running, you can access the API documentation at `http://localhost:8000/docs`.

### API Endpoints

- `GET /api/v1/version`: Get the VoxLogicA version
- `POST /api/v1/run`: Run a VoxLogicA program with various output options

## Development

### Running Tests

```bash
# Run tests
python -m unittest discover tests
```

## Features

- Modern Python API with type hints
- FastAPI-based REST API
- Typer-based CLI
- Lark-based parser for the imgql language
