# Dataset API: Dynamic VoxLogicA Compilation Implementation Plan

## Status: ACTIONABLE IMPLEMENTATION PLAN

## Core Concept

Implement dataset processing in VoxLogicA-2 using **dynamic function compilation** with **SHA256 CBA IDs** and **Dask delayed execution**. This approach enables interactive dataset operations while maintaining VoxLogicA-2's content-addressed execution model.

## Architecture: Dynamic Compilation After Dataset Loading

### Key Innovation: Delayed VoxLogicA Function Compilation

```
Dataset Loading (Dask delayed) → Function Compilation (Dask delayed) → Execution
```

**Critical Insight**: Use Dask's `@delayed` decorator to defer VoxLogicA function compilation until after dataset elements are loaded, enabling dynamic compilation with actual element CBA IDs.

### Enhanced VoxLogicA-2 Pipeline

```
VoxLogicA Source → Reducer (Environment + WorkPlan) → ExecutionEngine(environment=env) → Dynamic Compilation
                      ↑                                        ↓                           ↓
              Function definitions                    Static primitives              f(cba_id) per element
              stored as FunctionVal                  via PrimitivesLoader          using element's CBA ID
```

**Key Enhancement**: ExecutionEngine receives Environment at construction for dynamic function access.

## Implementation

### 1. Dynamic Function Compilation (Functional Style)

**File**: `implementation/python/voxlogica/dynamic_compilation.py`

```python
"""
Pure functional dynamic compilation for dataset operations
"""
from typing import Dict, Tuple
from voxlogica.reducer import WorkPlan, Environment, FunctionVal, OperationVal, reduce_expression, NodeId

def compile_function_with_element(
    environment: Environment,
    function_name: str,
    element_cba_id: NodeId
) -> Tuple[WorkPlan, NodeId]:
    """
    Dynamically compile f(element_cba_id) where element is represented by CBA ID
    
    Args:
        environment: Reducer's environment containing function definitions
        function_name: Name of function to compile  
        element_cba_id: CBA ID of dataset element to bind as function argument
        
    Returns:
        Tuple of (new_workplan, result_operation_id)
        
    Raises:
        RuntimeError: If function not found or invalid
    """
    # Get function definition from environment (no side effects)
    function_val = environment.try_find(function_name)
    if function_val is None:
        raise RuntimeError(f"Function '{function_name}' not found in environment")
    if not isinstance(function_val, FunctionVal):
        raise RuntimeError(f"'{function_name}' is not a function")
    
    # Ensure function has exactly one parameter for dataset element mapping
    if len(function_val.parameters) != 1:
        raise RuntimeError(f"Dataset map function '{function_name}' must have exactly 1 parameter, got {len(function_val.parameters)}")
    
    # Create new WorkPlan for this function compilation (immutable)
    dynamic_workplan = WorkPlan()
    
    # Create new environment with function parameter bound to element's CBA ID
    # This uses static (lexical) scoping - function_val.environment is the closure environment
    # and we extend it with the element binding
    parameter_name = function_val.parameters[0]
    temp_env = function_val.environment.bind(parameter_name, OperationVal(element_cba_id))
    
    # Reduce function body with element CBA ID as argument (pure function)
    result_id = reduce_expression(temp_env, dynamic_workplan, function_val.expression)
    
    return dynamic_workplan, result_id

def merge_workplans(base_workplan: WorkPlan, dynamic_workplan: WorkPlan) -> WorkPlan:
    """
    Merge dynamic operations into base workplan (functional style)
    
    Args:
        base_workplan: Original workplan
        dynamic_workplan: Dynamically compiled operations
        
    Returns:
        New WorkPlan with merged operations (immutable)
    """
    # Create new workplan with merged nodes (CBA IDs ensure deduplication)
    merged_nodes = {**base_workplan.nodes, **dynamic_workplan.nodes}
    merged_goals = base_workplan.goals + dynamic_workplan.goals
    merged_namespaces = base_workplan._imported_namespaces | dynamic_workplan._imported_namespaces
    
    return WorkPlan(
        nodes=merged_nodes,
        goals=merged_goals,
        _imported_namespaces=merged_namespaces
    )
```

### 2. Enhanced Execution Engine (Functional Style)

**File**: `implementation/python/voxlogica/execution.py` (modifications)

```python
class ExecutionEngine:
    """Execution engine with environment for dynamic compilation"""
    
    def __init__(self, 
                 storage_backend: Optional[StorageBackend] = None,
                 primitives_loader: Optional[PrimitivesLoader] = None,
                 environment: Optional[Environment] = None):
        """
        Initialize execution engine with environment for dynamic compilation
        
        Args:
            storage_backend: Storage backend for results
            primitives_loader: Primitives loader for operations
            environment: Reducer environment for dynamic function compilation
        """
        self.storage = storage_backend or get_storage()
        self.primitives = primitives_loader or PrimitivesLoader()
        self.environment = environment  # Optional: enables dynamic compilation
        self._active_executions: Dict[str, 'ExecutionSession'] = {}
        self._lock = threading.Lock()
    
    def execute_workplan(self, workplan: WorkPlan, execution_id: Optional[str] = None) -> ExecutionResult:
        """Execute workplan with dynamic compilation support"""
        # ... existing execution logic ...
        
        # Pass environment to session for dynamic compilation
        session = ExecutionSession(
            execution_id, 
            workplan, 
            self.storage, 
            self.primitives,
            environment=self.environment  # Enable dynamic compilation
        )
        
        # ... rest of existing execution logic ...
```

### 3. Dataset Map Primitive (Using Existing Abstractions)

**File**: `implementation/python/voxlogica/primitives/dataset/map.py`

```python
"""
Dataset mapping with dynamic VoxLogicA function compilation
"""
import dask.bag as db
from dask.delayed import delayed
from typing import Any

def execute(**kwargs) -> db.Bag:
    """
    Apply VoxLogicA function to each dataset element with dynamic compilation
    
    Args:
        **kwargs: VoxLogicA argument convention:
            '0': function_name (str) - Name of VoxLogicA function to apply
            '1': dataset (dask.bag.Bag) - Dataset to transform
            
    Returns:
        dask.bag.Bag: Transformed dataset with function applied to each element
    """
    function_name = kwargs['0']  # Follow VoxLogicA argument convention
    dataset = kwargs['1']
    
    if not isinstance(function_name, str):
        raise TypeError(f"Function name must be string, got {type(function_name)}")
    
    if not hasattr(dataset, 'map'):
        raise TypeError(f"Expected Dask bag, got {type(dataset)}")
    
    # Get execution engine context for dynamic compilation
    from voxlogica.execution import get_execution_engine
    engine = get_execution_engine()
    
    if not engine.environment:
        raise RuntimeError("Dynamic compilation requires environment - ensure ExecutionEngine has environment set")
    
    @delayed
    def dynamic_map_compilation(func_name: str, dataset_bag: db.Bag) -> db.Bag:
        """Compile VoxLogicA function after dataset loading (delayed execution)"""
        
        def compile_and_apply(element: Any) -> Any:
            """Compile and apply function to single dataset element"""
            from voxlogica.dynamic_compilation import compile_function_with_element, merge_workplans
            
            # Element becomes a constant in the workplan - use existing CBA ID computation
            element_workplan = WorkPlan()
            element_cba_id = element_workplan.add_node(ConstantValue(element))
            
            # Dynamically compile f(element_cba_id) using reducer's environment
            dynamic_workplan, result_id = compile_function_with_element(
                engine.environment,
                func_name, 
                element_cba_id
            )
            
            # Merge element constant into dynamic workplan
            merged_workplan = merge_workplans(element_workplan, dynamic_workplan)
            
            # Execute the compiled function and return result
            execution_result = engine.execute_workplan(merged_workplan)
            if not execution_result.success:
                raise RuntimeError(f"Dynamic compilation failed for element: {execution_result.failed_operations}")
            
            # Retrieve result from storage using result_id
            return engine.storage.retrieve(result_id)
        
        # Apply dynamic compilation to each element
        return dataset_bag.map(compile_and_apply)
    
    # Return delayed compilation - execution happens when .compute() is called
    return dynamic_map_compilation(function_name, dataset)
```

### 4. ReadDir Primitive (Simplified)

**File**: `implementation/python/voxlogica/primitives/dataset/readdir.py`

```python
"""
Directory loading as Dask bag for dataset processing
"""
import dask.bag as db
from pathlib import Path

def execute(**kwargs) -> db.Bag:
    """
    Load directory contents as Dask bag of file paths
    
    Args:
        **kwargs: VoxLogicA argument convention:
            '0': directory_path (str) - Path to directory to scan
            '1': pattern (str, optional) - Glob pattern (default: "*")
            
    Returns:
        dask.bag.Bag: Bag containing absolute file paths as strings
    """
    directory_path = kwargs['0']
    pattern = kwargs.get('1', '*')
    
    if not isinstance(directory_path, str):
        raise TypeError(f"Directory path must be string, got {type(directory_path)}")
    
    path = Path(directory_path)
    if not path.exists():
        raise ValueError(f"Directory does not exist: {directory_path}")
    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {directory_path}")
    
    files = [str(f.absolute()) for f in path.glob(pattern) if f.is_file()]
    npartitions = max(1, min(len(files) // 100, 10))
    
    return db.from_sequence(files, npartitions=npartitions)
```

### 5. Modified Reducer Integration (Functional Style)

**File**: `implementation/python/voxlogica/features.py` (modifications)

```python
def execute_voxlogica_program_with_environment(source_code: str) -> ExecutionResult:
    """
    Execute VoxLogicA program with environment support for dynamic compilation
    
    Args:
        source_code: VoxLogicA source code
        
    Returns:
        ExecutionResult with execution status
    """
    from voxlogica.parser import parse_program
    from voxlogica.reducer import reduce_program_with_environment
    from voxlogica.execution import ExecutionEngine
    
    # Parse program
    program = parse_program(source_code)
    
    # Reduce program and capture environment
    environment, workplan = reduce_program_with_environment(program)
    
    # Create execution engine with environment for dynamic compilation
    engine = ExecutionEngine(environment=environment)
    
    # Execute with dynamic compilation support
    return engine.execute_workplan(workplan)

# Extend reducer to return environment
def reduce_program_with_environment(program: Program) -> Tuple[Environment, WorkPlan]:
    """
    Reduce program and return both environment and workplan
    
    Args:
        program: Parsed VoxLogicA program
        
    Returns:
        Tuple of (final_environment, workplan)
    """
    from voxlogica.reducer import reduce_command, Environment, WorkPlan
    
    workplan = WorkPlan()
    env = Environment()
    parsed_imports: Set[str] = set()
    
    # Process commands and track environment changes
    commands = list(program.commands)
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, workplan, parsed_imports, command)
        commands = imports + commands
    
    return env, workplan
```

### 6. Namespace Structure

**File**: `implementation/python/voxlogica/primitives/dataset/__init__.py`

```python
"""Dataset processing primitives with dynamic VoxLogicA compilation"""

def register_primitives():
    """Static primitive discovery - readdir.py and map.py auto-discovered"""
    return {}
```

## Key Design Decisions Resolved

### 1. Functional Programming Style
- **Pure functions**: `compile_function_with_element` has no side effects
- **Immutable data**: Creates new WorkPlan instances instead of modifying existing ones
- **Environment passing**: ExecutionEngine receives Environment at construction, not via setter

### 2. Existing Abstraction Reuse
- **CBA ID computation**: Uses existing `WorkPlan.add_node()` and `_compute_node_id()`
- **Argument convention**: Follows VoxLogicA's `kwargs` with string keys ("0", "1", etc.)
- **Environment binding**: Uses existing `Environment.bind()` with static scoping
- **WorkPlan merging**: Uses functional merge instead of mutating workplans

### 3. Static (Lexical) Scoping
- `FunctionVal.environment` contains the closure environment
- New bindings extend the closure environment without modifying it
- Parameter binding follows existing `Environment.bind()` semantics

### 4. No WorkPlan Merging in ExecutionEngine
- Dynamic compilation creates separate WorkPlans
- Each function call gets its own execution context
- CBA ID system ensures automatic deduplication across executions

## Usage Examples

### Basic Dataset Processing
```voxlogica
import "dataset"

// Define transformation function
let enhance_image(img) = gaussian_blur(brightness(img, 1.2), 2.0)

// Load and process dataset
let images = dataset.readDir("/data/medical_scans", "*.nii.gz")
let enhanced = dataset.map(enhance_image, images)
```

### Interactive Execution Pattern
```python
# Each function call gets independent compilation and execution
>>> dataset = readDir("/data", "*.jpg")          # Dask bag of file paths
>>> enhanced = map(enhance_func, dataset)        # Dynamic compilation per element
>>> result = enhanced.compute()                  # Execute entire pipeline
```

## Implementation Checklist

### Core Infrastructure
- [ ] Create `implementation/python/voxlogica/dynamic_compilation.py`
- [ ] Modify `ExecutionEngine.__init__()` to accept `environment` parameter
- [ ] Extend reducer to return environment via `reduce_program_with_environment()`
- [ ] Create `dataset.readDir` primitive
- [ ] Create `dataset.map` primitive with dynamic compilation

### Integration
- [ ] Modify main execution pipeline to pass environment to ExecutionEngine
- [ ] Test dynamic compilation with real VoxLogicA functions
- [ ] Verify CBA ID consistency and deduplication
- [ ] Validate functional programming patterns

This implementation maintains VoxLogicA-2's architectural principles while enabling powerful dynamic dataset processing capabilities.
