# WorkPlan Refactoring: From Class Methods to Converter Functions

## Overview

The `WorkPlan` class has been refactored from a traditional class with conversion methods to a dataclass with external converter functions. This change improves separation of concerns and follows Python best practices.

## Changes Made

### 1. WorkPlan Converted to Dataclass

**Before:**
```python
class WorkPlan:
    def __init__(self):
        self.operations: Dict[OperationId, Operation] = {}
        self.goals: List[OperationId] = []
    
    def to_json(self, buffer_assignment=None) -> dict:
        # conversion logic
    
    def to_dot(self, buffer_assignment=None) -> str:
        # conversion logic
```

**After:**
```python
@dataclass
class WorkPlan:
    operations: Dict[OperationId, Operation]
    goals: List[OperationId]
    
    def __init__(self):
        self.operations = {}
        self.goals = []
```

### 2. Converters Package Structure

Created a new `converters` package with the following structure:

```
voxlogica/converters/
├── __init__.py          # Public API exports
├── json_converter.py    # JSON conversion functionality
└── dot_converter.py     # DOT (Graphviz) conversion functionality
```

### 3. New Usage Pattern

**Before:**
```python
from voxlogica.reducer import reduce_program

work_plan = reduce_program(program)
json_data = work_plan.to_json()
dot_data = work_plan.to_dot()
```

**After:**
```python
from voxlogica.reducer import reduce_program
from voxlogica.converters import to_json, to_dot

work_plan = reduce_program(program)
json_data = to_json(work_plan)
dot_data = to_dot(work_plan)
```

## Benefits

### 1. **Separation of Concerns**
- WorkPlan focuses solely on data representation
- Conversion logic is separated into specialized modules
- Each converter handles one format exclusively

### 2. **Extensibility**
- Easy to add new converters (XML, YAML, GraphML, etc.)
- New formats don't require modifying the core WorkPlan class
- Each converter can be developed and tested independently

### 3. **Maintainability**
- Clear module boundaries
- Easier to locate format-specific code
- Reduced coupling between data structure and serialization

### 4. **Testing**
- Converters can be unit tested independently
- Mock/stub WorkPlan objects for converter testing
- Cleaner test organization

## Converter Functions

### `to_json(work_plan, buffer_assignment=None) -> dict`

Converts a WorkPlan to a JSON-serializable dictionary.

**Parameters:**
- `work_plan`: The WorkPlan instance to convert
- `buffer_assignment`: Optional mapping of operation IDs to buffer IDs

**Returns:** Dictionary suitable for JSON serialization

### `to_dot(work_plan, buffer_assignment=None) -> str`

Converts a WorkPlan to DOT (Graphviz) format.

**Parameters:**
- `work_plan`: The WorkPlan instance to convert  
- `buffer_assignment`: Optional mapping of operation IDs to buffer IDs

**Returns:** DOT format string for graph visualization

## Migration Guide

### For Users of the Library

Replace method calls with function calls:

```python
# Old way
json_data = work_plan.to_json()
dot_data = work_plan.to_dot()

# New way
from voxlogica.converters import to_json, to_dot

json_data = to_json(work_plan)
dot_data = to_dot(work_plan)
```

### For Contributors

When adding new conversion formats:

1. Create a new module in `voxlogica/converters/`
2. Implement the conversion function
3. Export it in `voxlogica/converters/__init__.py`
4. Add tests in the appropriate test file

Example for a new XML converter:

```python
# voxlogica/converters/xml_converter.py
def to_xml(work_plan, buffer_assignment=None) -> str:
    """Convert WorkPlan to XML format"""
    # implementation

# voxlogica/converters/__init__.py
from .xml_converter import to_xml
__all__ = ['to_json', 'to_dot', 'to_xml']
```

## Architecture Benefits

This refactoring follows several software engineering principles:

- **Single Responsibility Principle**: Each converter has one job
- **Open/Closed Principle**: Easy to extend with new formats without modifying existing code
- **Dependency Inversion**: WorkPlan doesn't depend on specific conversion implementations
- **Interface Segregation**: Clients only import the converters they need

The new architecture is more maintainable, testable, and extensible while preserving all existing functionality.
