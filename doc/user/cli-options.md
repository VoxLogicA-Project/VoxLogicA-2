# VoxLogicA-2 Command Line Options & Usage Guide

This guide describes the command line interfaces (CLI) for the Python port of VoxLogicA-2. It is intended for end users who wish to run VoxLogicA-2 from the command line, automate workflows, or understand the available options.

---

## Python Port CLI

The Python port provides a CLI using [Typer](https://typer.tiangolo.com/). The main entry point is typically `python -m voxlogica.main` or via an installed script (e.g., `voxlogica`).

### Basic Usage

```sh
python -m voxlogica.main [COMMAND] [OPTIONS]
```

### Commands and Options

#### `run`

Run a VoxLogicA session file and process the workflow.

**Usage:**

```sh
python -m voxlogica.main run <filename> [OPTIONS]
```

| Option                                | Type | Description                                      |
| ------------------------------------- | ---- | ------------------------------------------------ |
| `<filename>`                          | str  | VoxLogicA session file (required)                |
| `--save-task-graph <file>`            | str  | Save the task graph to a file                    |
| `--save-task-graph-as-dot <file>`     | str  | Save the task graph in .dot format and exit      |
| `--save-task-graph-as-ast <file>`     | str  | Save the task graph in AST format and exit       |
| `--save-task-graph-as-program <file>` | str  | Save the task graph in VoxLogicA format and exit |
| `--save-syntax <file>`                | str  | Save the AST in text format and exit             |
| `--save-task-graph-as-json <file>`    | str  | Save the task graph as JSON and exit             |
| `--execute` / `--no-execute`          | flag | Execute the workplan (default: --execute)        |
| `--debug`                             | flag | Enable debug mode (more verbose logging)         |

**Example:**

```sh
python -m voxlogica.main run example.imgql --save-task-graph graph.txt --debug
```

#### `serve`

Start the VoxLogicA API server (FastAPI).

**Usage:**

```sh
python -m voxlogica.main serve [OPTIONS]
```

| Option    | Type | Description                 |
| --------- | ---- | --------------------------- |
| `--host`  | str  | Host to bind the API server |
| `--port`  | int  | Port to bind the API server |
| `--debug` | flag | Enable debug mode           |

**Example:**

```sh
python -m voxlogica.main serve --host 0.0.0.0 --port 8080 --debug
```

#### `version`

Print the VoxLogicA version and exit.

**Usage:**

```sh
python -m voxlogica.main version
```

---

### Basic Usage

```sh
VoxLogicA.exe [OPTIONS] <filename>
```

### Options

| Option                                | Type    | Description                                      |
| ------------------------------------- | ------- | ------------------------------------------------ |
| `--version`                           | flag    | Print the VoxLogicA version and exit             |
| `--save-task-graph <file>`            | str/opt | Save the task graph to a file                    |
| `--save-task-graph-as-dot <file>`     | str     | Save the task graph in .dot format and exit      |
| `--save-task-graph-as-ast <file>`     | str/opt | Save the task graph in AST format and exit       |
| `--save-task-graph-as-program <file>` | str/opt | Save the task graph in VoxLogicA format and exit |
| `--save-syntax <file>`                | str/opt | Save the AST in text format and exit             |
| `--save-task-graph-as-json <file>`    | str     | Save the task graph as JSON and exit             |
| `<filename>`                          | str     | VoxLogicA session file (required)                |

**Example:**

```sh
VoxLogicA.exe --save-task-graph graph.txt --save-task-graph-as-dot graph.dot example.imgql
```

---

## Notes

- All options are case-sensitive.
- For both ports, the session file (`<filename>`) is required unless using the `version` command.
- Output files will be overwritten if they already exist.
- Debug mode provides additional logging for troubleshooting.

For further help, use the `--help` flag with any command.
