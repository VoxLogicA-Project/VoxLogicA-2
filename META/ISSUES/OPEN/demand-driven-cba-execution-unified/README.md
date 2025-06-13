# Interactive Content-Addressed Execution Engine

## Date
2025-06-13

## Status
- [ ] **DESIGN COMPLETE** - Ready for implementation

## Problem Statement

VoxLogicA-2's current execution engine has fundamental architectural limitations that prevent scalable interactive computation:

### Current Issues

1. **Batch Execution**: Programs processed entirely upfront via `reduce_program()` → `execute_workplan()`, preventing interactive computation
2. **Memory Accumulation**: All nodes stored permanently in `WorkPlan.nodes`, causing unbounded memory growth  
3. **Function Symbol String Conversion**: `ConstantValue(expr.identifier)` violates functional programming principles
4. **No Result Persistence**: Computation results lost between sessions despite existing CBA storage
5. **Forest of DAGs Problem**: Dataset operations like `map(f, dataset)` create thousands of independent DAGs, amplifying memory issues

### Evidence

- `features.py` calls `reduce_program()` then `execute_workplan()` in batch mode
- Line 179 in `reducer.py` uses `ConstantValue(expr.identifier)` for function symbols
- `WorkPlan.nodes: Dict[NodeId, Node]` stores all nodes permanently
- Dataset operations create new `WorkPlan()` per element, accumulating memory

## Target Architecture: Interactive CBA Execution

### Core Principles

1. **Interactive Execution**: Computation triggered only by goals (`print`, `save`)
2. **Goal-Based Memory Management**: Reference counting tracks active computations only
3. **Content-Addressed Persistence**: Results cached across sessions via CBA storage
4. **Function Abstractions**: Proper closure capture instead of string conversion
5. **Bounded Memory**: Memory automatically cleaned up when goals complete

### Execution Model

```python
# Interactive session with bounded memory
session = InteractiveExecutionEngine(storage_backend)

# Goal 1: Compute result - intermediates cleaned after goal completion
result1 = session.execute("let x = expensive_computation()")

# Goal 2: Reuse previous result - loaded on-demand from storage
result2 = session.execute("let y = x + 10")  

# Dataset processing: streaming with per-element cleanup
result3 = session.execute("let mapped = dataset.map(huge_dataset, transform_func)")
```

## Technical Design

### 1. Function Abstractions

Replace string-based function symbols with proper abstractions:

```python
@dataclass
class FunctionAbstraction:
    """Function with closure environment"""
    parameter: str
    body_node_id: NodeId  # CBA ID of function body AST
    closure_env: Dict[str, NodeId]  # variable -> CBA ID mapping
    
    def get_cba_id(self) -> NodeId:
        """Compute deterministic CBA ID including closure"""
        components = {
            "type": "function_abstraction",
            "parameter": self.parameter,
            "body": self.body_node_id,
            "closure": sorted(self.closure_env.items())  # Deterministic order
        }
        return compute_sha256_hash(components)
```

### 2. Interactive Execution Session

Goal-based execution with automatic memory management:

```python
@dataclass
class InteractiveExecutionSession:
    """Manages memory for interactive execution"""
    storage: StorageBackend
    active_results: Dict[NodeId, Any] = field(default_factory=dict)  # Memory cache
    reference_counts: Dict[NodeId, int] = field(default_factory=dict)  # Active refs
    node_definitions: Dict[NodeId, Node] = field(default_factory=dict)  # Session nodes
    
    def execute_goal(self, goal_node_id: NodeId) -> Any:
        """Execute goal with automatic cleanup"""
        # 1. Build dependency graph and initialize reference counts
        self._start_computation(goal_node_id)
        
        # 2. Compute goal (loads dependencies on-demand)
        result = self._compute_node(goal_node_id)
        
        # 3. Persist goal result
        self.storage.store(goal_node_id, result)
        
        # 4. Trigger cascade cleanup (goal ref count → 0)
        self._complete_goal(goal_node_id)
        
        return result
    
    def _compute_node(self, node_id: NodeId) -> Any:
        """Compute node with dependency management"""
        # Check memory cache first
        if node_id in self.active_results:
            return self.active_results[node_id]
        
        # Check persistent storage
        if self.storage.exists(node_id):
            result = self.storage.retrieve(node_id)
            self.active_results[node_id] = result
            return result
        
        # Compute node
        node = self.node_definitions[node_id]
        
        if isinstance(node, ConstantValue):
            result = node.value
            
        elif isinstance(node, Operation):
            # Compute dependencies
            args = {}
            for arg_name, dep_id in node.arguments.items():
                args[arg_name] = self._compute_node(dep_id)
            
            # Execute operation
            result = self._execute_operation(node.operator, args)
            
        elif isinstance(node, FunctionAbstraction):
            result = node  # Functions are values
            
        # Cache result and manage references
        self.active_results[node_id] = result
        
        # Decrement dependency reference counts
        if isinstance(node, Operation):
            for dep_id in node.arguments.values():
                self._decrement_reference(dep_id)
                
        return result
    
    def _complete_goal(self, goal_node_id: NodeId) -> None:
        """Complete goal and trigger cleanup cascade"""
        self._decrement_reference(goal_node_id)
        
    def _decrement_reference(self, node_id: NodeId) -> None:
        """Decrement reference count and cleanup if unreferenced"""
        if node_id not in self.reference_counts:
            return
            
        self.reference_counts[node_id] -= 1
        
        if self.reference_counts[node_id] <= 0:
            # Remove from memory (persisted results remain accessible)
            self.active_results.pop(node_id, None)
            self.reference_counts.pop(node_id, None)
```

### 3. Dataset Operations with Streaming Cleanup

Handle forest of DAGs pattern efficiently:

```python
class StreamingDatasetProcessor:
    """Process datasets with per-element cleanup"""
    
    def process_map_operation(self, function_node_id: NodeId, dataset: List[Any]) -> List[Any]:
        """Process map(f, dataset) with bounded memory"""
        results = []
        
        for element in dataset:
            # Create minimal session for this element
            element_session = InteractiveExecutionSession(self.storage)
            
            # Create element node and function application
            element_node = ConstantValue(element)
            element_cba_id = element_node.get_cba_id()
            element_session.node_definitions[element_cba_id] = element_node
            
            apply_node = Operation(
                operator="apply",
                arguments={"function": function_node_id, "argument": element_cba_id}
            )
            apply_cba_id = apply_node.get_cba_id()
            element_session.node_definitions[apply_cba_id] = apply_node
            
            # Execute with automatic cleanup
            result = element_session.execute_goal(apply_cba_id)
            results.append(result)
            
            # Session cleanup happens automatically
            
        return results
    
    def process_element_access(self, collection_node_id: NodeId, index: int) -> Any:
        """Access single element without loading entire collection"""
        # Create access operation
        access_node = Operation(
            operator="access_element",
            arguments={"collection": collection_node_id, "index": str(index)}
        )
        access_cba_id = access_node.get_cba_id()
        
        # Check if already computed
        if self.storage.exists(access_cba_id):
            return self.storage.retrieve(access_cba_id)
        
        # Compute just this element
        session = InteractiveExecutionSession(self.storage)
        session.node_definitions[access_cba_id] = access_node
        
        return session.execute_goal(access_cba_id)
```

### 4. Primitive Integration

Enable VoxLogicA primitives to interact with the execution engine:

```python
class MapPrimitive:
    """Map primitive that integrates with interactive execution"""
    
    def __init__(self, execution_engine: InteractiveExecutionSession):
        self.engine = execution_engine
        
    def execute(self, function_cba_id: NodeId, dataset_cba_id: NodeId) -> NodeId:
        """Execute map(f, dataset) and return result CBA ID"""
        
        # Get dataset from storage
        dataset = self.engine.storage.retrieve(dataset_cba_id)
        
        # Process with streaming cleanup
        processor = StreamingDatasetProcessor(self.engine.storage)
        results = processor.process_map_operation(function_cba_id, dataset)
        
        # Create result node and return its CBA ID
        result_node = ConstantValue(results)
        result_cba_id = result_node.get_cba_id()
        
        # Store result
        self.engine.storage.store(result_cba_id, results)
        
        return result_cba_id
```

## Implementation Plan

### Phase 1: Foundation (Week 1)
1. **Implement FunctionAbstraction**: Replace string-based function symbols
2. **Create InteractiveExecutionSession**: Basic goal-based execution
3. **Add reference counting**: Track active computations only

### Phase 2: Core Engine (Week 2)  
1. **Implement memory management**: Goal-based cleanup with cascade
2. **Add on-demand loading**: Integrate with existing CBA storage
3. **Update primitive integration**: Enable map operation with new engine

### Phase 3: Dataset Support (Week 3)
1. **Implement StreamingDatasetProcessor**: Handle forest of DAGs
2. **Add element access optimization**: Compute only needed elements
3. **Integrate with existing dataset operations**: Update map primitive

### Phase 4: Integration (Week 4)
1. **Update reducer**: Minimal changes to support FunctionAbstraction
2. **Replace execution engine**: Swap in InteractiveExecutionSession
3. **Add session management**: CLI and API integration

## Key Benefits

1. **Bounded Memory**: Memory usage independent of dataset size
2. **Interactive Performance**: Only active computations consume memory
3. **Result Persistence**: All computations cached across sessions via CBA
4. **Functional Purity**: Proper function abstractions with closures
5. **Scalable Datasets**: Forest of DAGs handled efficiently with streaming

## Success Criteria

1. **Memory**: Constant memory usage regardless of dataset size
2. **Performance**: Results cached and reused across sessions
3. **Correctness**: All existing VoxLogicA programs execute correctly
4. **Interactivity**: Support for incremental computation and exploration
5. **Simplicity**: Clean functional design with minimal complexity

This design provides a complete roadmap for implementing interactive CBA execution with automatic memory management, enabling VoxLogicA-2 to scale to arbitrarily large datasets while maintaining bounded memory usage and persistent result caching.
## Documentation

- **README.md**: This comprehensive design document (unified from previous separate documents)
- **PROMPT.md**: AI agent implementation instructions for execution engine rewrite
