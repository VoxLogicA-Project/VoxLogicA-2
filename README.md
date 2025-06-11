# VoxLogicA 2.0.0-alpha.0.2

This is the source code of the new iteration of the spatial model checker VoxLogicA. The current implementation includes:

- VoxLogicA program parsing and analysis
- Task graph generation and optimization
- Multiple export formats (JSON, DOT)
- Unified CLI and REST API interfaces

## Quick Start

There's a convenience script in the root directory to run VoxLogicA:

```bash
# Run VoxLogicA without manually activating the virtual environment
./voxlogica run test.imgql

# Show help for the main CLI
./voxlogica --help

# Show help for a subcommand (e.g., run)
./voxlogica run --help

# Show version
./voxlogica version

# Start API server
./voxlogica serve
```

This script automatically activates the virtual environment and runs the Python implementation.

## CLI Reference

### Main Command

```
./voxlogica [OPTIONS] COMMAND [ARGS]...
```

- `--help` : Show the main help message and exit.

#### Commands:
- `version` : Show the VoxLogicA version
- `run` : Run a VoxLogicA program
- `serve` : Start the VoxLogicA API server

### `run` Command

```
./voxlogica run [OPTIONS] FILENAME
```

- `FILENAME` (required): VoxLogicA session file to run

Options:
- `--save-task-graph <file>`: Save the task graph
- `--save-task-graph-as-dot <file>`: Save the task graph in .dot format and exit
- `--save-task-graph-as-json <file>`: Save the task graph as JSON and exit
- `--save-syntax <file>`: Save the AST in text format and exit
- `--compute-memory-assignment`: Compute and display memory buffer assignments
- `--execute` / `--no-execute`: Execute the workplan (default: --execute)
- `--debug`: Enable debug mode
- `--help`: Show help for this command and exit

### `serve` Command

```
./voxlogica serve [OPTIONS]
```

Options:
- `--host <host>`: Host to bind the API server (default: 127.0.0.1)
- `--port <port>`: Port to bind the API server (default: 8000)
- `--debug`: Enable debug mode
- `--help`: Show help for this command and exit

### `version` Command

```
./voxlogica version [OPTIONS]
```

Options:
- `--help`: Show help for this command and exit

---

For detailed documentation, see:

- Implementation documentation: `implementation/python/README.md`
- API usage guide: `doc/user/api-usage.md`
