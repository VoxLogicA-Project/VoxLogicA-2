"""Interactive REPL session support for VoxLogicA."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voxlogica.execution import ExecutionEngine
from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.parser import (
    Declaration,
    Import,
    Print,
    Save,
    parse_expression_content,
    parse_program_content,
)
from voxlogica.reducer import reduce_program
from voxlogica.storage import (
    MATERIALIZED_STATUS,
    ResultsDatabase,
    get_storage,
)


@dataclass(frozen=True)
class ReplValue:
    """Evaluation payload produced by the REPL session."""

    node_id: str
    value: Any
    persisted: bool
    persisted_repr_only: bool


@dataclass(frozen=True)
class ReplProgramResult:
    """Outcome for loaded/entered command blocks."""

    declarations_added: int
    goals_executed: int
    goals_skipped: int


class ReplSession:
    """Incremental REPL session over a shared declaration/import context."""

    def __init__(
        self,
        strategy: str = "dask",
        engine: ExecutionEngine | None = None,
        storage: ResultsDatabase | None = None,
        sequence_preview_limit: int = 20,
    ):
        self.strategy = strategy
        self.storage = storage or get_storage()
        self.engine = engine or ExecutionEngine(storage_backend=self.storage)
        self.sequence_preview_limit = max(1, sequence_preview_limit)
        self._context_commands: list[str] = []

    @property
    def context_commands(self) -> tuple[str, ...]:
        return tuple(self._context_commands)

    def reset(self) -> None:
        self._context_commands.clear()

    def context_program(self) -> str:
        return "\n".join(self._context_commands)

    def load_file(self, filename: str | Path, execute_goals: bool = False) -> ReplProgramResult:
        path = Path(filename)
        if not path.exists():
            raise FileNotFoundError(f"REPL load failed: file not found: {path}")
        return self.execute_program(path.read_text(encoding="utf-8"), execute_goals=execute_goals)

    def execute_input(self, text: str) -> ReplValue | ReplProgramResult:
        candidate = text.strip()
        if not candidate:
            raise ValueError("Empty REPL input")

        try:
            program = parse_program_content(candidate)
        except Exception:
            return self.evaluate_expression(candidate)

        if not program.commands:
            return self.evaluate_expression(candidate)

        return self.execute_program(candidate, execute_goals=True)

    def execute_program(self, program_text: str, execute_goals: bool = False) -> ReplProgramResult:
        program = parse_program_content(program_text)
        declarations_added = 0
        goals_executed = 0
        goals_skipped = 0

        for command in program.commands:
            syntax = command.to_syntax()
            if isinstance(command, (Declaration, Import)):
                self._context_commands.append(syntax)
                declarations_added += 1
                continue

            if isinstance(command, (Print, Save)):
                if execute_goals:
                    self._execute_goal_with_context(syntax)
                    goals_executed += 1
                else:
                    goals_skipped += 1
                continue

            raise ValueError(f"Unsupported REPL command type: {type(command).__name__}")

        return ReplProgramResult(
            declarations_added=declarations_added,
            goals_executed=goals_executed,
            goals_skipped=goals_skipped,
        )

    def evaluate_expression(self, expression: str) -> ReplValue:
        parse_expression_content(expression)

        label = "__repl_result__"
        command = f'print "{label}" {expression}'
        workplan = self._reduce_with_context(command)
        plan = workplan.to_symbolic_plan()
        if not plan.goals:
            raise RuntimeError("REPL evaluation produced no goals")

        goal_id = plan.goals[-1].id
        prepared = self.engine.compile_plan(workplan, strategy=self.strategy)

        preview_page = self.engine.page(
            prepared,
            goal_id,
            offset=0,
            limit=self.sequence_preview_limit,
            strategy=self.strategy,
        )
        raw_value = prepared.materialization_store.get(goal_id)
        shown_value = self._render_value(raw_value, preview_page.items)

        persisted, repr_only = self._persist_value(goal_id, raw_value, expression)
        return ReplValue(
            node_id=goal_id,
            value=shown_value,
            persisted=persisted,
            persisted_repr_only=repr_only,
        )

    def _reduce_with_context(self, command: str):
        full_program = self.context_program()
        if full_program:
            full_program = f"{full_program}\n{command}"
        else:
            full_program = command
        return reduce_program(parse_program_content(full_program))

    def _execute_goal_with_context(self, command: str) -> None:
        workplan = self._reduce_with_context(command)
        result = self.engine.execute_workplan(workplan, strategy=self.strategy)
        if not result.success:
            errors = "; ".join(
                f"{node_id}: {message}" for node_id, message in result.failed_operations.items()
            )
            raise RuntimeError(f"REPL goal execution failed: {errors}")

    def _render_value(self, raw_value: Any, preview_items: list[Any]) -> Any:
        if isinstance(raw_value, SequenceValue):
            return preview_items
        if hasattr(raw_value, "compute") and callable(raw_value.compute):
            return preview_items
        if isinstance(raw_value, (list, tuple, range)):
            return preview_items
        return raw_value

    def _persist_value(self, node_id: str, raw_value: Any, expression: str) -> tuple[bool, bool]:
        metadata = {
            "source": "repl",
            "strategy": self.strategy,
            "expression": expression,
        }

        try:
            self.storage.put_success(node_id, raw_value, metadata=metadata)
            record = self.storage.get_record(node_id)
            return bool(record and record.status == MATERIALIZED_STATUS), False
        except Exception:
            repr_payload = {
                "repr": repr(raw_value),
                "type": type(raw_value).__name__,
            }
            try:
                self.storage.put_success(
                    node_id,
                    repr_payload,
                    metadata={**metadata, "representation_only": True},
                )
                record = self.storage.get_record(node_id)
                return bool(record and record.status == MATERIALIZED_STATUS), True
            except Exception:
                return False, True


def run_interactive_repl(strategy: str = "dask") -> int:
    """Run an interactive terminal REPL session."""
    session = ReplSession(strategy=strategy)

    print(f"VoxLogicA REPL [{strategy}]")
    print("Commands: :help | :load <file> | :run <file> | :show | :reset | :quit")

    while True:
        try:
            line = input("voxlogica> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0

        if not line:
            continue

        if line in {":quit", ":exit"}:
            return 0

        if line == ":help":
            print("Enter declarations/imports/commands or plain expressions.")
            print(":load <file>   load declarations/imports from file")
            print(":run <file>    execute file goals and import declarations/imports")
            print(":show          show active session context")
            print(":reset         clear session context")
            print(":quit          exit REPL")
            continue

        if line == ":show":
            context = session.context_program()
            if context:
                print(context)
            else:
                print("(empty context)")
            continue

        if line == ":reset":
            session.reset()
            print("context cleared")
            continue

        if line.startswith(":load "):
            filename = line[len(":load ") :].strip()
            load_result = session.load_file(filename, execute_goals=False)
            print(
                "loaded declarations="
                f"{load_result.declarations_added}, skipped_goals={load_result.goals_skipped}"
            )
            continue

        if line.startswith(":run "):
            filename = line[len(":run ") :].strip()
            run_result = session.load_file(filename, execute_goals=True)
            print(
                "loaded declarations="
                f"{run_result.declarations_added}, executed_goals={run_result.goals_executed}"
            )
            continue

        try:
            result = session.execute_input(line)
            if isinstance(result, ReplValue):
                mode = "repr" if result.persisted_repr_only else "value"
                persisted = "persisted" if result.persisted else "not-persisted"
                print(f"[{result.node_id[:12]}] {result.value} ({persisted}:{mode})")
            else:
                print(
                    "ok declarations="
                    f"{result.declarations_added}, goals={result.goals_executed}"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}")
