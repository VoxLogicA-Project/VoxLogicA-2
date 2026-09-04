"""Typed boundaries that retain the original exception as their cause."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PrimitiveExecutionError(RuntimeError):
    """A primitive failed with enough safe context for classification."""

    operation: str
    arguments: tuple[Any, ...]

    def __str__(self) -> str:
        return f"{self.operation} failed"


@dataclass
class NodeExecutionError(RuntimeError):
    """A kernel failure associated with a symbolic node."""

    node_id: str
    operation: str

    def __str__(self) -> str:
        return f"{self.operation} failed while evaluating node {self.node_id[:12]}"
