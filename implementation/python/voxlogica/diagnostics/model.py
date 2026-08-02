"""Serializable diagnostic data; rendering and exception handling live elsewhere."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class SourceSpan:
    """One source location, deliberately separate from content-addressed nodes."""

    source_name: str
    line: int | None = None
    column: int | None = None
    line_text: str | None = None

    @classmethod
    def from_location(cls, location: str | None, source_text: str | None = None) -> "SourceSpan | None":
        if not location:
            return None
        parts = location.rsplit(":", 2)
        if len(parts) != 3:
            return cls(location)
        source_name, line_text, column_text = parts
        if not line_text.isdigit() or not column_text.isdigit():
            return cls(location)
        line = int(line_text)
        source_line = None
        if source_text:
            lines = source_text.splitlines()
            if 1 <= line <= len(lines):
                source_line = lines[line - 1]
        return cls(source_name, line, int(column_text), source_line)


@dataclass(frozen=True)
class Diagnostic:
    """A stable, safe-to-render description of one user-visible problem."""

    code: str
    title: str
    message: str
    severity: Severity = "error"
    hint: str | None = None
    span: SourceSpan | None = None
    operation: str | None = None
    node_id: str | None = None
    safe_context: dict[str, str] = field(default_factory=dict)
    details_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DiagnosticReport:
    """Diagnostic plus technical evidence retained outside normal terminal output."""

    diagnostic: Diagnostic
    traceback_text: str
    cause_chain: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "diagnostic": self.diagnostic.to_dict(),
            "traceback": self.traceback_text,
            "cause_chain": list(self.cause_chain),
        }
