# VoxLogicA 2.0.0-alpha.0.2

⚠️ **Pre-Alpha Software**: This software is in pre-alpha stage and we constantly make breaking changes as there are no users yet.

VoxLogicA is a next-generation spatial model checker and computational imaging platform that combines:

- **Functional programming language** with mathematical notation
- **Content-addressed execution** for automatic memoization and reproducibility  
- **Dynamic compilation** enabling interactive dataset processing
- **Medical imaging primitives** with SimpleITK integration
- **Distributed computing** via Dask for large-scale data analysis

This is the source code of the new iteration of the spatial model checker VoxLogicA. The current implementation includes:

- VoxLogicA program parsing and analysis
- Task graph generation and optimization
- Multiple export formats (JSON, DOT)
- Unified CLI and REST API interfaces
- **Dataset API** with dynamic compilation support for interactive data processing
- **Medical imaging integration** with SimpleITK primitives
- **Function symbols as first-class citizens** in functional operations
- **Content-addressed execution** with automatic memoization and caching

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

## Language Features

### Dataset Operations

VoxLogicA supports interactive dataset processing with dynamic compilation:

```imgql
// Load directory contents as a dataset
let files = dataset.readdir("/path/to/data")

// Define custom processing function
let add_ten(x) = x + 10

// Apply function to each element (function symbols as first-class citizens)
let result = dataset.map(files, add_ten)

// Print results
print "processed" result

// Save results in various formats
save "output.json" result  // JSON with automatic Dask bag serialization
```

### Medical Imaging

Built-in SimpleITK integration for medical image analysis:

```imgql
import "simpleitk"

// Load medical image
let img = ReadImage("scan.nii.gz")

// Apply threshold (unqualified names supported)
let thresholded = BinaryThreshold(img, 150, 99999, 1, 0)

// Compute statistics
let stats = simpleitk.MinimumMaximum(thresholded)
print "max_value" index(stats, 1)

// Save in medical imaging formats
save "output.png" thresholded
```

### Content-Addressed Execution

All computations are automatically memoized using content-addressed storage, enabling efficient incremental processing and reproducible results.

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

## Documentation

For detailed information, see:

- **Implementation guide**: `implementation/python/README.md`
- **API usage guide**: `doc/user/api-usage.md`
- **Development documentation**: `META/GUIDE.md`
- **Recent changes**: `META/CHANGES/`
- **Closed issues and features**: `META/ISSUES/CLOSED/`

### Key Recent Features

- ✅ **Dataset API implementation** - Interactive data processing with dynamic compilation
- ✅ **Function symbol support** - First-class function citizens in `dataset.map`
- ✅ **JSON serialization for Dask bags** - Seamless data export capabilities
- ✅ **SimpleITK namespace simplification** - Unqualified medical imaging function names
- ✅ **Unified execution architecture** - Single consistent execution path with dynamic capabilities

For architectural details and future roadmap, see `META/ISSUES/OPEN/demand-driven-cba-execution-unified/`.
