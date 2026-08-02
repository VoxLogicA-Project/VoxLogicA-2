"""Render diagnostics at CLI/REPL boundaries, never from worker code."""

from __future__ import annotations

import json

from voxlogica.diagnostics.model import Diagnostic, DiagnosticReport


def render_diagnostic(diagnostic: Diagnostic) -> str:
    lines = [f"error[{diagnostic.code}]: {diagnostic.title}", ""]
    if diagnostic.span and diagnostic.span.line is not None:
        span = diagnostic.span
        location = f"{span.source_name}:{span.line}:{span.column or 1}"
        lines.extend([f"  --> {location}"])
        if span.line_text:
            marker = " " * max((span.column or 1) - 1, 0) + "^"
            lines.extend([f"{span.line} | {span.line_text}", f"  | {marker}"])
    lines.append(diagnostic.message)
    if diagnostic.hint:
        lines.extend(["", f"Hint: {diagnostic.hint}"])
    if diagnostic.details_id:
        lines.extend(["", f"Details: voxlogica errors show {diagnostic.details_id}"])
    return "\n".join(lines)


def render_report_json(report: DiagnosticReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)
