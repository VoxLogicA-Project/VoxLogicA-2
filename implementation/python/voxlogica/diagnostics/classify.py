"""Exception classifiers.  Keep library-specific parsing isolated here."""

from __future__ import annotations

from pathlib import Path
import errno
import os
import traceback
from typing import Iterable

from voxlogica.diagnostics.exceptions import NodeExecutionError, PrimitiveExecutionError
from voxlogica.diagnostics.model import Diagnostic, DiagnosticReport, SourceSpan


def _unwrap(error: BaseException) -> tuple[BaseException, str | None, str | None, tuple[object, ...]]:
    node_id = operation = None
    arguments: tuple[object, ...] = ()
    current = error
    while True:
        if isinstance(current, NodeExecutionError):
            node_id, operation = current.node_id, current.operation
        if isinstance(current, PrimitiveExecutionError):
            operation, arguments = current.operation, current.arguments
        if current.__cause__ is None:
            return current, node_id, operation, arguments
        current = current.__cause__


def _cause_chain(error: BaseException) -> tuple[str, ...]:
    chain: list[str] = []
    current: BaseException | None = error
    while current is not None:
        chain.append(f"{type(current).__name__}: {current}")
        current = current.__cause__
    return tuple(chain)


def _read_image_diagnostic(path_value: object, *, span: SourceSpan | None,
                           node_id: str | None, operation: str | None) -> Diagnostic:
    path = Path(str(path_value))
    context = {"path": str(path)}
    if not path.exists():
        return Diagnostic(
            code="E_IMAGE_NOT_FOUND", title="Cannot open image",
            message=f"Path does not exist: {path}",
            hint="Check the filename and dataset root.", span=span,
            node_id=node_id, operation=operation, safe_context=context,
        )
    if not path.is_file():
        return Diagnostic(
            code="E_IMAGE_UNREADABLE", title="Cannot open image",
            message=f"Path is not a regular file: {path}",
            hint="Pass an image file, not a directory.", span=span,
            node_id=node_id, operation=operation, safe_context=context,
        )
    if not os.access(path, os.R_OK):
        return Diagnostic(
            code="E_PERMISSION_DENIED", title="Cannot read image",
            message=f"Read permission is denied: {path}",
            hint="Check file ownership and read permissions.", span=span,
            node_id=node_id, operation=operation, safe_context=context,
        )
    return Diagnostic(
        code="E_IMAGE_UNREADABLE", title="Cannot read image",
        message=f"SimpleITK could not read: {path}",
        hint="Check that the file is a supported, non-corrupt image and that you can read it.",
        span=span, node_id=node_id, operation=operation, safe_context=context,
    )


def build_report(error: BaseException, *, locations: Iterable[str] = (),
                 source_text: str | None = None) -> DiagnosticReport:
    """Classify an exception without exposing its traceback in normal output."""
    root, node_id, operation, arguments = _unwrap(error)
    span = next((SourceSpan.from_location(location, source_text) for location in locations if location), None)
    if operation == "ReadImage" and arguments:
        diagnostic = _read_image_diagnostic(arguments[0], span=span, node_id=node_id, operation=operation)
    elif isinstance(root, PermissionError):
        diagnostic = Diagnostic(
            "E_PERMISSION_DENIED", "Permission denied", str(root),
            hint="Check read/write permissions for the referenced path.", span=span,
            operation=operation, node_id=node_id,
        )
    elif isinstance(root, FileNotFoundError):
        diagnostic = Diagnostic(
            "E_FILE_NOT_FOUND", "File not found", str(root),
            hint="Check the filename and working directory.", span=span,
            operation=operation, node_id=node_id,
        )
    elif isinstance(root, OSError) and root.errno == errno.ENOSPC:
        diagnostic = Diagnostic(
            "E_STORAGE_FULL", "Storage is full", "VoxLogicA2 could not write required data.",
            hint="Free disk space or use a different cache/output location.", span=span,
            operation=operation, node_id=node_id,
        )
    elif isinstance(root, MemoryError):
        diagnostic = Diagnostic(
            "E_OUT_OF_MEMORY", "Out of memory", "VoxLogicA2 could not allocate required memory.",
            hint="Reduce concurrency, image size, or cache pressure.", span=span,
            operation=operation, node_id=node_id,
        )
    elif isinstance(root, IndexError):
        diagnostic = Diagnostic(
            "E_INDEX_OUT_OF_RANGE", "Index is out of range", str(root),
            hint="Check sequence bounds and zero-based indexing.", span=span,
            operation=operation, node_id=node_id,
        )
    elif isinstance(root, (ValueError, TypeError)):
        message = str(root)
        lowered = message.lower()
        if "selects no voxels" in lowered or "empty mask" in lowered:
            code, title, hint = "E_EMPTY_SELECTION", "Selection is empty", "Adjust the mask or threshold."
        elif "same number of voxels" in lowered or "same size" in lowered or "dimension" in lowered:
            code, title, hint = "E_IMAGE_MISMATCH", "Images are incompatible", "Check image dimensions, geometry, and pixel types."
        else:
            code, title, hint = "E_INVALID_ARGUMENT", "Invalid operation input", "Check the primitive arguments and image compatibility."
        diagnostic = Diagnostic(
            code, title, message, hint=hint, span=span,
            operation=operation, node_id=node_id,
        )
    else:
        diagnostic = Diagnostic(
            "E_INTERNAL", "VoxLogicA2 execution failed", f"{type(root).__name__}: {root}",
            hint="Use the details id for the technical cause chain.", span=span,
            operation=operation, node_id=node_id,
        )
    return DiagnosticReport(
        diagnostic=diagnostic,
        traceback_text="".join(traceback.format_exception(error)),
        cause_chain=_cause_chain(error),
    )
