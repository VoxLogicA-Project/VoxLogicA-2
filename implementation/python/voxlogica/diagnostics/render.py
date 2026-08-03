"""Render diagnostics at CLI/REPL boundaries, never from worker code."""

from __future__ import annotations

import json
import os
import sys

from voxlogica.diagnostics.model import Diagnostic, DiagnosticReport


_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GREEN = "\x1b[32m"
_CYAN = "\x1b[36m"


def color_enabled(*, stream=None) -> bool:
    """Use emphasis in terminals, while keeping pipes and CI machine-readable."""
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    return bool(getattr(stream or sys.stderr, "isatty", lambda: False)())


def _style(text: str, *codes: str, color: bool) -> str:
    return f"{''.join(codes)}{text}{_RESET}" if color else text


def render_diagnostic(diagnostic: Diagnostic, *, color: bool | None = None) -> str:
    """Render a high-contrast, copy-safe diagnostic for people at a terminal."""
    color = color_enabled() if color is None else color
    headline = "error" if diagnostic.severity == "error" else diagnostic.severity
    lines = [
        f"{_style(headline, _BOLD, _RED if diagnostic.severity == 'error' else _YELLOW, color=color)}"
        f"{_style(f'[{diagnostic.code}]', _BOLD, color=color)}: "
        f"{_style(diagnostic.title, _BOLD, color=color)}",
        "",
    ]
    if diagnostic.span and diagnostic.span.line is not None:
        span = diagnostic.span
        location = f"{span.source_name}:{span.line}:{span.column or 1}"
        lines.extend([f"  {_style('-->', _BOLD, _CYAN, color=color)} {location}"])
        if span.line_text:
            marker = " " * max((span.column or 1) - 1, 0) + "^"
            lines.extend([
                f"{_style(str(span.line), _DIM, color=color)} | {span.line_text}",
                f"  | {_style(marker, _BOLD, _RED, color=color)}",
            ])
    lines.append(_style(diagnostic.message, _BOLD, color=color))
    if diagnostic.hint:
        lines.extend(["", f"{_style('Hint:', _BOLD, _GREEN, color=color)} {diagnostic.hint}"])
    if diagnostic.details_id:
        command = f"voxlogica errors show {diagnostic.details_id}"
        lines.extend([
            "",
            f"{_style('Details:', _BOLD, _CYAN, color=color)} "
            f"{_style(command, _BOLD, color=color)}",
        ])
    return "\n".join(lines)


def render_legacy_error_block(block: str, *, color: bool | None = None) -> str:
    """Give parser/static errors the same visual hierarchy during migration."""
    color = color_enabled() if color is None else color
    lines: list[str] = []
    for line in block.splitlines():
        if " error: " in line:
            prefix, message = line.split(" error: ", 1)
            lines.append(
                f"{_style(prefix, _CYAN, color=color)} "
                f"{_style('error:', _BOLD, _RED, color=color)} "
                f"{_style(message, _BOLD, color=color)}"
            )
        elif line.startswith("E_") and ": " in line:
            prefix, message = line.split(": ", 1)
            lines.append(
                f"{_style(prefix, _BOLD, _RED, color=color)}: "
                f"{_style(message, _BOLD, color=color)}"
            )
        elif line.lstrip().startswith("^"):
            indent = line[: len(line) - len(line.lstrip())]
            lines.append(f"{indent}{_style('^', _BOLD, _RED, color=color)}")
        else:
            lines.append(line)
    return "\n".join(lines)


def render_report_json(report: DiagnosticReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)
