"""Stable, user-facing diagnostics for VoxLogicA2 execution surfaces."""

from voxlogica.diagnostics.classify import build_report
from voxlogica.diagnostics.exceptions import NodeExecutionError, PrimitiveExecutionError
from voxlogica.diagnostics.model import Diagnostic, DiagnosticReport, SourceSpan
from voxlogica.diagnostics.render import render_diagnostic, render_report_json

__all__ = [
    "Diagnostic",
    "DiagnosticReport",
    "NodeExecutionError",
    "PrimitiveExecutionError",
    "SourceSpan",
    "build_report",
    "render_diagnostic",
    "render_report_json",
]
