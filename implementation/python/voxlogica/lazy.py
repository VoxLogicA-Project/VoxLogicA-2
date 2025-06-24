"""
Lazy compilation infrastructure for VoxLogicA WorkPlans.

This module provides the LazyCompilation dataclass and related utilities
for deferred expression compilation with parameter substitution.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from voxlogica.parser import Expression
    from voxlogica.reducer import Environment, NodeId, Stack

@dataclass
class LazyCompilation:
    """Represents a deferred compilation of an expression with parameter substitution."""
    expression: Expression  # AST expression to compile (e.g., f(x))
    environment: 'Environment'  # Environment at compilation time
    parameter_bindings: Dict[str, 'NodeId']  # Runtime parameter substitutions
    compilation_id: str = ""  # For debugging and tracking
    
    def can_compile(self, available_results: Dict['NodeId', Any]) -> bool:
        """Check if all parameter dependencies are satisfied."""
        return all(dep in available_results for dep in self.parameter_bindings.values())


@dataclass
class ForLoopCompilation:
    """Represents a deferred compilation of a for loop with Dask bag expansion."""
    variable: str  # Loop variable name
    iterable_expr: Expression  # Expression that evaluates to an iterable (e.g., range(10))
    body_expr: Expression  # Loop body expression
    environment: 'Environment'  # Environment at compilation time
    stack: 'Stack'  # Call stack for error reporting
    
    def __str__(self) -> str:
        return f"for {self.variable} in {self.iterable_expr} do {self.body_expr}"
