# VoxLogicA DAG Core

This package now contains only the modules needed to parse VoxLogicA programs,
build the symbolic DAG, export that DAG, and execute it locally.

## Usage

```bash
./voxlogica run program.imgql
./voxlogica run program.imgql --no-execute --save-task-graph-as-dot graph.dot
./voxlogica list-primitives
```
