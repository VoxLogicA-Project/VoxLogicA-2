# VS Code MCP Setup For The UI Inspector

VoxLogicA ships a Playwright-backed MCP server that can inspect the rendered UI, operate the running app, read and replace the live Start program, and query the backend runtime through the same attached browser session.

## Prerequisites

- The Python environment for this repository must include the pinned dependencies from `implementation/python/requirements.txt`.
- Playwright Chromium must be installed once:

```bash
.venv/bin/playwright install chromium
```

- Run the app you want to inspect. For the current dev workflow that is usually:

```bash
./voxlogica dev
```

The default frontend URL in dev mode is `http://127.0.0.1:5173/`.

## Manual Launch

From the repository root:

```bash
./voxlogica mcp ui-inspector --url http://127.0.0.1:5173/
```

Useful flags:

- `--headed`: show the browser window instead of running headless
- `--browser-channel chrome`: use a named Playwright browser channel
- `--viewport-width` / `--viewport-height`: control the page viewport

## VS Code MCP Configuration

Add a stdio MCP server entry that points at the repository CLI. In a local VS Code MCP config, the shape is:

```json
{
  "servers": {
    "voxlogica-ui": {
      "type": "stdio",
      "command": "/Users/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python",
      "args": [
        "-m",
        "voxlogica.main",
        "mcp",
        "ui-inspector",
        "--url",
        "http://127.0.0.1:5173/"
      ],
      "cwd": "/Users/vincenzo/data/local/repos/VoxLogicA-2",
      "env": {
        "PYTHONPATH": "/Users/vincenzo/data/local/repos/VoxLogicA-2/implementation/python"
      }
    }
  }
}
```

If you prefer the wrapper script, this also works:

```json
{
  "servers": {
    "voxlogica-ui": {
      "type": "stdio",
      "command": "/Users/vincenzo/data/local/repos/VoxLogicA-2/voxlogica",
      "args": [
        "mcp",
        "ui-inspector",
        "--url",
        "http://127.0.0.1:5173/"
      ],
      "cwd": "/Users/vincenzo/data/local/repos/VoxLogicA-2"
    }
  }
}
```

## Action Areas And Tools

The server is organized by action area so agents can move between browser automation, UI state, live program editing, and runtime inspection without guessing which layer to use.

### Browser

- `open_page`: navigate the inspector browser to a URL
- `inspect_page`: return a DOM excerpt, visible interactive elements, and action-area hints
- `focus_app`: bring the attached app window to the front
- `close_browser`: dispose the Playwright browser session

### UI

- `inspect_app_state`: read the live app shell state, including active tab and Start tab automation state
- `select_app_tab`: switch the running app to a tab such as `start`, `graph`, `results`, or `compute-log`
- `click_element`: click an element by CSS selector
- `focus_element`: move browser focus to an element
- `read_element_text`: read text from a visible element such as the Start code editor
- `type_text`: type into an input or textarea
- `select_option`: choose a value in a `<select>`

### Program

- `read_program`: read the current Start editor program from the running app
- `set_program`: replace the current Start editor program and optionally run it
- `click_variable`: select a variable token in the Start tab as if it were clicked in the editor

### Runtime

- `inspect_runtime_state`: combine app state, current program, jobs, and optionally symbols or graph in one response
- `list_playground_jobs`: list queued or running playground jobs
- `get_playground_job`: inspect a single job by id
- `kill_playground_job`: cancel a single job by id
- `get_program_symbols`: resolve symbols for the live editor program or an explicit program text
- `get_program_graph`: resolve the graph for the live editor program or an explicit program text
- `resolve_program_value`: call the live backend value endpoint with the current editor program by default
- `resolve_program_value_page`: call the live backend paged value endpoint with the current editor program by default

## Typical Workflow

1. Start `./voxlogica dev`
2. Start or register the MCP server
3. Call `inspect_page` or `inspect_app_state` to confirm attachment
4. Use `read_program`, `click_variable`, or `select_app_tab` for app-aware control
5. Use `inspect_runtime_state` or the `resolve_program_*` tools when you need queue, graph, symbol, or page-level runtime data