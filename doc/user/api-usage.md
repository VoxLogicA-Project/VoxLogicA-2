# VoxLogicA API Usage Guide

## Overview

VoxLogicA features a **unified CLI-API design** where every CLI command has a corresponding API endpoint with identical functionality. This ensures complete feature parity between command-line and programmatic usage. The API is built using FastAPI and provides automatic documentation, request validation, and consistent error handling.

## Starting the API Server

To start the VoxLogicA API server:

```bash
# Start with default settings (localhost:8000)
./voxlogica serve

# Start with custom host and port
./voxlogica serve --host 0.0.0.0 --port 8080

# Start with debug mode enabled
./voxlogica serve --debug
```

Once started, the API will be available at:

- **API Base URL**: `http://localhost:8000/api/v1/`
- **Interactive Documentation**: `http://localhost:8000/docs`
- **OpenAPI Schema**: `http://localhost:8000/openapi.json`

## CLI to API Mapping

| CLI Command                          | API Endpoint          | Description                  |
| ------------------------------------ | --------------------- | ---------------------------- |
| `voxlogica version`                  | `GET /api/v1/version` | Get VoxLogicA version        |
| `voxlogica run [options] file.imgql` | `POST /api/v1/run`    | Run program with all options |
| `voxlogica list-primitives`          | `GET /api/v1/primitives` | List primitives and namespaces |
| `serve playground`                   | `POST /api/v1/playground/jobs` | Start async playground execution |
| `serve playground`                   | `GET /api/v1/playground/jobs/{job_id}` | Poll playground job status |
| `serve playground`                   | `DELETE /api/v1/playground/jobs/{job_id}` | Kill running/stale playground job |
| `serve playground`                   | `POST /api/v1/playground/symbols` | Parse/static resolution + diagnostics for editor |
| `serve playground`                   | `POST /api/v1/playground/value` | On-demand value materialization/inspection |
| `serve gallery`                      | `GET /api/v1/docs/gallery` | Return markdown + parsed playground examples |
| `serve quality dashboard`            | `GET /api/v1/testing/report` | JUnit + coverage + perf report snapshot |
| `serve quality dashboard`            | `GET /api/v1/testing/performance/chart` | Latest vox1-vs-vox2 perf SVG |
| `serve quality dashboard`            | `GET /api/v1/testing/performance/primitive-chart` | Per-primitive benchmark histogram SVG |
| `serve quality dashboard`            | `POST /api/v1/testing/jobs` | Start interactive test run (quick/full/perf) |
| `serve quality dashboard`            | `GET /api/v1/testing/jobs/{job_id}` | Poll interactive test run + log tail |
| `serve quality dashboard`            | `DELETE /api/v1/testing/jobs/{job_id}` | Kill interactive test run |
| `serve storage dashboard`            | `GET /api/v1/storage/stats` | Cache/storage statistics |

## Available Endpoints

## Serve Policy Defaults

- Serve/API always runs with non-legacy policy (`legacy=false`).
- Playground execution strategy is forced to `dask` for `/playground/jobs` and `/playground/value`.
- Read primitives are restricted to configured roots:
  - `VOXLOGICA_SERVE_DATA_DIR` (primary root)
  - `VOXLOGICA_SERVE_EXTRA_READ_ROOTS` (optional comma-separated roots)
- Unknown callable names fail during static resolution (before execution).

### 1. Get Version

Get the current VoxLogicA version.

**CLI Equivalent**: `voxlogica version`

**Endpoint**: `GET /api/v1/version`

**Example**:

```bash
curl http://localhost:8000/api/v1/version
```

**Response**:

```json
{
  "version": "2.0.0a2"
}
```

### 2. Run Program

Run a VoxLogicA program with various output options. This endpoint mirrors the CLI `run` command exactly, supporting all the same options and combinations.

**CLI Equivalent**: `voxlogica run [options] file.imgql`

**Endpoint**: `POST /api/v1/run`

**Request Body**:

```json
{
  "program": "let a = 1\nlet b = 2\nlet c = a + b\nprint \"sum\" c",
  "filename": "optional_filename.imgql",
  "save_task_graph": "output.dot",
  "save_task_graph_as_json": "output.json",
  "save_syntax": "syntax.txt",
  "debug": false
}
```

### Playground Symbols Diagnostics Schema

`POST /api/v1/playground/symbols`

- Success payload:
  - `available: true`
  - `program_hash`
  - `symbol_table`
  - `print_targets`
  - `diagnostics: []`
- Static failure payload:
  - `available: false`
  - `diagnostics: [{code, message, location?, symbol?}]`

Example diagnostic response:

```json
{
  "available": false,
  "program_hash": "8b4f0a...",
  "operations": 0,
  "goals": 0,
  "symbol_table": {},
  "print_targets": [],
  "diagnostics": [
    {
      "code": "E_UNKNOWN_CALLABLE",
      "message": "Unknown callable: UnknownCallable",
      "symbol": "UnknownCallable"
    }
  ]
}
```

**All fields except `program` are optional:**

- `program` (required): The VoxLogicA program source code
- `filename` (optional): Filename for error reporting (CLI mode only)
- `save_task_graph` (optional): Filename for DOT export (API returns content under this key, CLI saves to this file)
- `save_task_graph_as_dot` (optional): Alternative name for DOT export
- `save_task_graph_as_json` (optional): Filename for JSON export (API returns content under this key, CLI saves to this file)
- `save_syntax` (optional): Filename for syntax export (API returns content under this key, CLI saves to this file)
- `execute` (optional): Execute the workplan (default: true)
- `debug` (optional): Enable debug mode

**Important:** The API returns export content in the response `saved_files` field using the provided filenames as keys, while the CLI saves to the actual files.

**Basic Example**:

```bash
curl -X POST http://localhost:8000/api/v1/run \
  -H "Content-Type: application/json" \
  -d '{
    "program": "let a = 1\nlet b = 2\nlet c = a + b\nprint \"sum\" c"
  }'
```

**Example with Multiple Exports**:

```bash
curl -X POST http://localhost:8000/api/v1/run \
  -H "Content-Type: application/json" \
  -d '{
    "program": "let a = 1\nlet b = 2\nlet c = a + b\nprint \"sum\" c",
    "save_task_graph_as_json": "result.json",
    "save_task_graph": "result.dot",
    "save_syntax": "result.txt",
    "debug": true
  }'
```

**Response**:

```json
{
  "operations": 3,
  "goals": 1,
  "task_graph": "goals: print(sum,2)\noperations:\n0 -> 1.0\n1 -> 2.0\n2 -> +(0,1)",
  "syntax": "let a=1.0\nlet b=2.0\nlet c=+(a,b)\nprint \"sum\" c",
  "saved_files": {
    "result.dot": "digraph {\n  0 [label=\"[0] 1.0\"]\n  1 [label=\"[1] 2.0\"]\n  2 [label=\"[2] +(0,1)\"]\n  0 -> 2;\n  1 -> 2;\n}\n",
    "result.json": {
      "operations": [
        { "operator": 1.0, "arguments": [] },
        { "operator": 2.0, "arguments": [] },
        { "operator": "+", "arguments": [0, 1] }
      ],
      "goals": [{ "operation_id": 2 }]
    },
    "result.txt": "let a=1.0\nlet b=2.0\nlet c=+(a,b)\nprint \"sum\" c"
  }
}
```

**Export Content Format** (available in `saved_files` field):

```json
{
  "operations": [
    {
      "operator": 1.0,
      "arguments": []
    },
    {
      "operator": 2.0,
      "arguments": []
    },
    {
      "operator": "+",
      "arguments": [0, 1]
    }
  ],
  "goals": [
    {
      "type": "print",
      "name": "sum",
      "operation_id": 2
    }
  ]
}
```

## Error Handling

The API uses standard HTTP status codes and returns consistent error responses:

### 400 Bad Request

Returned when there's an error in the VoxLogicA program or invalid request data.

**Example Error Response**:

```json
{
  "detail": "Unexpected token Token('__ANON_0', 'invalid') at line 1, column 5."
}
```

### 422 Unprocessable Entity

Returned when the request body doesn't match the expected schema.

**Example Error Response**:

```json
{
  "detail": {
    "error": "Invalid request data: field required"
  }
}
```

### 500 Internal Server Error

Returned when there's an unexpected server error.

**Example Error Response**:

```json
{
  "detail": "Internal server error"
}
```

## Authentication

Currently, the VoxLogicA API does not require authentication. All endpoints are publicly accessible.

## Rate Limiting

No rate limiting is currently implemented. Consider implementing rate limiting in production environments.

## Content Types

- **Request Content-Type**: `application/json`
- **Response Content-Type**: `application/json`

## API Client Examples

### Python with requests

```python
import requests
import json

# Start the server first: ./voxlogica serve

base_url = "http://localhost:8000/api/v1"

# Get version
response = requests.get(f"{base_url}/version")
print(f"Version: {response.json()['version']}")

# Analyze a program
program_data = {
    "program": "let x = 5\nlet y = x * 2\nprint \"result\" y",
    "filename": "example.imgql"
}

response = requests.post(f"{base_url}/run", json=program_data)
if response.status_code == 200:
    result = response.json()
    print(f"Operations: {result['operations']}")
    print(f"Goals: {result['goals']}")
    print(f"Task Graph:\n{result['task_graph']}")
else:
    print(f"Error: {response.json()['detail']}")

# Analyze with exports
export_data = {
    "program": "let a = 1\nlet b = 2\nprint \"sum\" (a + b)",
    "save_task_graph_as_json": "output.json",
    "save_task_graph": "output.dot"
}

response = requests.post(f"{base_url}/run", json=export_data)
if response.status_code == 200:
    result = response.json()
    print(f"Operations: {result['operations']}")

    # Access saved files
    if 'saved_files' in result:
        for filename, content in result['saved_files'].items():
            print(f"File {filename}:")
            if isinstance(content, dict):
                print(json.dumps(content, indent=2))
            else:
                print(content)
else:
    print(f"Error: {response.json()['detail']}")
```

### JavaScript with fetch

```javascript
const baseUrl = "http://localhost:8000/api/v1";

// Get version
async function getVersion() {
  const response = await fetch(`${baseUrl}/version`);
  const data = await response.json();
  console.log(`Version: ${data.version}`);
}

// Analyze a program
async function analyzeProgram() {
  const programData = {
    program: 'let x = 5\nlet y = x * 2\nprint "result" y',
    filename: "example.imgql",
  };

  const response = await fetch(`${baseUrl}/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(programData),
  });

  if (response.ok) {
    const result = await response.json();
    console.log(`Operations: ${result.operations}`);
    console.log(`Goals: ${result.goals}`);
    console.log(`Task Graph:\n${result.task_graph}`);
  } else {
    const error = await response.json();
    console.error(`Error: ${error.detail}`);
  }
}

// Save task graph as JSON
async function saveTaskGraph() {
  const saveData = {
    program: "let a = 1\nlet b = 2\nprint a + b",
    filename: "output.json",
  };

  const response = await fetch(`${baseUrl}/save-task-graph-json`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(saveData),
  });

  if (response.ok) {
    const result = await response.json();
    console.log(result.message);
  } else {
    const error = await response.json();
    console.error(`Error: ${error.detail}`);
  }
}
```

### curl Examples

```bash
# Get version
curl http://localhost:8000/api/v1/version

# Analyze a program
curl -X POST http://localhost:8000/api/v1/program \
  -H "Content-Type: application/json" \
  -d '{"program": "let x = 5\nprint x"}'

# Save task graph as DOT
curl -X POST http://localhost:8000/api/v1/save-task-graph \
  -H "Content-Type: application/json" \
  -d '{"program": "let x = 5\nprint x", "filename": "graph.dot"}'

# Save task graph as JSON
curl -X POST http://localhost:8000/api/v1/save-task-graph-json \
  -H "Content-Type: application/json" \
  -d '{"program": "let x = 5\nprint x", "filename": "graph.json"}'
```

## Interactive Documentation

The API provides interactive documentation powered by Swagger UI. When the server is running, visit:

`http://localhost:8000/docs`

This interface allows you to:

- Explore all available endpoints
- View request/response schemas
- Test endpoints directly from the browser
- Download the OpenAPI specification

## Integration with CLI

The API and CLI share the same underlying feature system, ensuring complete feature parity. Any functionality available in the CLI is also available through the API, and vice versa.

**CLI to API Mapping**:

- `./voxlogica version` → `GET /api/v1/version`
- `./voxlogica run program.imgql` → `POST /api/v1/program`
- `./voxlogica run program.imgql --save-task-graph graph.dot` → `POST /api/v1/save-task-graph`
- `./voxlogica run program.imgql --save-task-graph-as-json graph.json` → `POST /api/v1/save-task-graph-json`

## Production Considerations

When deploying the VoxLogicA API in production:

1. **Security**: Consider adding authentication and authorization
2. **Rate Limiting**: Implement rate limiting to prevent abuse
3. **CORS**: Configure CORS settings for web applications
4. **Logging**: Enable detailed logging for monitoring and debugging
5. **Error Handling**: Implement custom error pages and logging
6. **Performance**: Consider using a production ASGI server like Gunicorn with Uvicorn workers
7. **Monitoring**: Set up health checks and monitoring

Example production deployment:

```bash
# Install production dependencies
pip install gunicorn

# Run with Gunicorn
gunicorn implementation.python.voxlogica.main:api_app \
  -w 4 \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```
