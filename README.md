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

# Show help
./voxlogica --help

# Show version
./voxlogica version

# Start API server
./voxlogica serve
```

This script automatically activates the virtual environment and runs the Python implementation.

For detailed documentation, see:

- Implementation documentation: `implementation/python/README.md`
- API usage guide: `doc/user/api-usage.md`
