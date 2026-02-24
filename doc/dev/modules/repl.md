# repl.py - Interactive Session Runtime

## Canonical Code
- `implementation/python/voxlogica/repl.py`

## Purpose
`repl.py` provides an incremental interactive session model for CLI REPL and future GUI embedding.

Core capabilities:
1. Maintain a declaration/import context across user inputs.
2. Evaluate plain expressions on demand.
3. Execute `print`/`save` goals when explicitly entered.
4. Persist evaluated expression results to the configured results store.

## API Surface

1. `ReplSession`
- `execute_input(text)`
- `evaluate_expression(expression)`
- `execute_program(program_text, execute_goals=False)`
- `load_file(path, execute_goals=False)`
- `reset()`
- `context_program()`

2. Result models
- `ReplValue`
- `ReplProgramResult`

3. CLI runner
- `run_interactive_repl(strategy="dask")`

## REPL Commands

- `:help`
- `:load <file>`
- `:run <file>`
- `:show`
- `:reset`
- `:quit` / `:exit`

## Storage Contract

Expression results are persisted by node id via `ResultsDatabase`.
If a runtime value is not directly serializable, REPL persistence falls back to a representation payload (`repr` + `type`) so interactive workflows still capture a durable result record.
