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
voxlogica run path/to/program.imgql --save-task-graph-as-dot graph.dot
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

- `GET /version`: Get the VoxLogicA version
- `POST /program`: Parse and reduce a VoxLogicA program
- `POST /save-task-graph`: Parse, reduce, and save the task graph of a VoxLogicA program

## Development

### Running Tests

```bash
# Run tests
python -m unittest discover tests
```

## Features

- Full feature parity with the F# implementation
- Modern Python API with type hints
- FastAPI-based REST API
- Typer-based CLI
- Lark-based parser for the imgql language
