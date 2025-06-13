# AI Agent Implementation Prompt: Interactive CBA Execution Engine

## Task Overview

You are to reimplement VoxLogicA-2's execution engine from scratch following the design specified in `README.md`. You must also review and minimally modify the reducer module if absolutely necessary.

## Critical Requirements

### 1. What to Implement
- **Execution Engine**: Complete rewrite from scratch using the interactive CBA execution design
- **Reducer Changes**: Review for factual errors first, then minimal modifications ONLY if absolutely needed

### 2. What NOT to Touch
- **Parser**: Leave completely unchanged
- **CLI/API interfaces**: Leave completely unchanged  
- **Storage backend**: Leave completely unchanged
- **Other modules**: Leave completely unchanged unless they directly import the execution engine

### 3. Implementation Style Requirements
- **Plain Old Data**: Use dataclasses for all data structures
- **Functional Programming**: Pure functions, immutable data, minimal side effects
- **Simplicity**: No category theory, no lenses, no complex list operations
- **Readability**: Clear, straightforward code that any developer can understand

## Implementation Steps

### Step 1: Reducer Review and Minimal Fixes

**Review `implementation/python/voxlogica/reducer.py` for:**
1. **Factual Errors**: Incorrect logic, wrong type annotations, broken functionality
2. **Redundant Code**: Duplicate logic, unnecessary complexity
3. **Function Symbol Issue**: The `ConstantValue(expr.identifier)` conversion on line 179

**Actions Allowed:**
- Fix factual errors and bugs
- Remove redundant code
- Replace function symbol string conversion with proper `FunctionAbstraction` support
- **Nothing else** - minimal changes only

### Step 2: Execution Engine Complete Rewrite

**Replace `implementation/python/voxlogica/execution.py` with:**

#### Core Components

1. **FunctionAbstraction Dataclass**
```python
@dataclass
class FunctionAbstraction:
    """Function with closure environment"""
    parameter: str
    body_node_id: NodeId  
    closure_env: Dict[str, NodeId]
```

2. **InteractiveExecutionSession Dataclass**
```python
@dataclass  
class InteractiveExecutionSession:
    """Goal-based execution with automatic memory management"""
    storage: StorageBackend
    active_results: Dict[NodeId, Any] = field(default_factory=dict)
    reference_counts: Dict[NodeId, int] = field(default_factory=dict)
    node_definitions: Dict[NodeId, Node] = field(default_factory=dict)
```

3. **StreamingDatasetProcessor**
```python
class StreamingDatasetProcessor:
    """Handle dataset operations with per-element cleanup"""
```

#### Key Methods

1. **Goal Execution**: `execute_goal(goal_node_id: NodeId) -> Any`
   - Initialize reference counting for computation
   - Compute goal with on-demand dependency loading
   - Persist result to storage
   - Trigger cascade cleanup

2. **Node Computation**: `_compute_node(node_id: NodeId) -> Any`
   - Check memory cache first
   - Check persistent storage second
   - Compute if not found
   - Manage reference counting

3. **Dataset Processing**: Handle `map(f, dataset)` with bounded memory
   - Process elements individually
   - Clean up after each element
   - Stream results without accumulating memory

#### Primitive Integration

**Enable VoxLogicA primitives to call the execution engine:**
- Map primitive creates function applications
- Each primitive gets access to the execution session
- Function abstractions can be applied through the execution engine

### Step 3: Integration Requirements

1. **Update imports** in files that use the execution engine
2. **Maintain API compatibility** - same public interface as current execution engine
3. **Preserve all functionality** - every current capability must work

## Design Constraints from README.md

### Memory Management
- **Goal-Based Cleanup**: Reference counting tracks only active computations
- **Cascade Cleanup**: When goal completes, all intermediate results cleaned from memory
- **Persistent Access**: All results remain accessible via CBA storage

### Function Handling
- **No String Conversion**: Functions are proper abstractions with closures
- **CBA ID Computation**: Include closure environment in deterministic hash
- **Application Mechanism**: Functions can be applied within the execution engine

### Dataset Operations
- **Streaming Processing**: Handle arbitrary dataset sizes with bounded memory
- **Per-Element Cleanup**: Each element processed independently with cleanup
- **Forest of DAGs**: Thousands of independent computations handled efficiently

## Success Criteria

1. **All Tests Pass**: Every existing test must continue to work
2. **Memory Bounded**: Memory usage independent of dataset size
3. **Functional Correctness**: All VoxLogicA programs execute correctly
4. **Clean Architecture**: Simple, readable functional code with dataclasses
5. **Primitive Integration**: Map and other primitives work with function abstractions

## Code Style Guidelines

### Dataclass Usage
```python
@dataclass
class ExampleNode:
    """Clear docstring"""
    field1: str
    field2: int
    optional_field: Optional[str] = None
```

### Function Style
```python
def pure_function(input_data: InputType) -> OutputType:
    """Pure function with clear input/output"""
    # Simple, readable logic
    return result
```

### Error Handling
```python
# Clear error messages with context
if condition_failed:
    raise ValueError(f"Clear description of what went wrong: {context}")
```

## Implementation Order

1. **Review reducer.py** - Fix errors, minimal changes only
2. **Implement core dataclasses** - FunctionAbstraction, InteractiveExecutionSession  
3. **Implement node computation** - Basic execution logic
4. **Add reference counting** - Memory management
5. **Implement dataset processing** - StreamingDatasetProcessor
6. **Update primitive integration** - Map operation with function abstractions
7. **Test and validate** - Ensure all functionality works

## Final Notes

- **Follow the README.md design exactly** - it contains the complete specification
- **Keep it simple** - prioritize readability and maintainability over cleverness
- **Minimal reducer changes** - only fix actual errors or add FunctionAbstraction support
- **Preserve all existing functionality** - this is a rewrite for better architecture, not feature changes
- **Focus on the execution engine** - this is the core deliverable

The goal is a clean, functional implementation that enables interactive CBA execution with bounded memory usage while maintaining full compatibility with existing VoxLogicA programs.
