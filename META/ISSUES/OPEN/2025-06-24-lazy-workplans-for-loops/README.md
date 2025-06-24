# Lazy WorkPlans and For Loops

**Status:** READY FOR IMPLEMENTATION  
**Priority:** HIGH (Phase 1) → MEDIUM (Phase 2)  
**Scope:** For loops only (map operations deferred)

## Goal

Implement for loops in VoxLogicA by making **WorkPlan purely lazy** - all compilation happens on-demand while preserving memoization.

## Core Concept

- **Purely lazy**: All WorkPlans compile expressions on-demand
- **Memoization preserved**: Same SHA256 hashes as before  
- **Triggered by access**: `.operations` property triggers compilation
- **No engine changes**: Execution engine unchanged

## Two-Phase Plan

### Phase 1: Make WorkPlan Purely Lazy ⚡ NEXT
- [ ] Add `LazyCompilation` dataclass
- [ ] Replace WorkPlan with purely lazy implementation
- [ ] All operations are lazy by default
- [ ] Update all tests to work with lazy WorkPlan

### Phase 2: For Loop Syntax ⏳ BLOCKED
- [ ] Add `for item in dataset { ... }` syntax
- [ ] Implement parser and AST support
- [ ] Use Phase 1 infrastructure for compilation
- [ ] **Create `range()` primitive** returning Dask bags
- [ ] **Integrate Dask collections** with lazy compilation

## Key Implementation

```python
@dataclass
class LazyCompilation:
    expression: Expression
    environment: Environment  
    parameter_bindings: Dict[str, NodeId]

# WorkPlan is now purely lazy - no eager compilation
@dataclass
class WorkPlan:
    nodes: Dict[NodeId, Node] = field(default_factory=dict)
    goals: List[Goal] = field(default_factory=list)
    lazy_compilations: List[LazyCompilation] = field(default_factory=dict)
    _expanded: bool = False
    
    @property
    def operations(self):
        if not self._expanded:
            self._expand_and_compile_all()  # Always lazy
        return {k: v for k, v in self.nodes.items() if isinstance(v, Operation)}
```

## Example

```voxlogica
// Note: range() primitive returns Dask bag
for n in range(5) {
    let fib = fibonacci(n)  // Compiled when n is known from Dask iteration
    save "result_" + n fib
}
```

**Result**: `fibonacci(2)` gets same SHA256 hash whether called directly or via loop.

## Files to Modify

**Phase 1:**
- `implementation/python/voxlogica/lazy.py` (NEW)
- `implementation/python/voxlogica/reducer.py` (REPLACE WorkPlan)  
- `tests/` (UPDATE all existing tests)

**Phase 2:**
- `implementation/python/voxlogica/parser/` (EXTEND)
- `implementation/python/voxlogica/ast_nodes.py` (EXTEND)

## Success Criteria

**Phase 1:**
- ✅ All existing tests updated and passing
- ✅ No performance regression
- ✅ Same SHA256 hashes - purely lazy implementation

**Phase 2:**
- ✅ For loop syntax works
- ✅ Efficient with large datasets
- ✅ Perfect memoization compatibility

## Dataset Assumptions

**For Phase 2**, datasets will be **Dask collections**:

1. **Dask bags**: `range(5)` → `dask.bag.from_sequence([0, 1, 2, 3, 4])`
2. **Lazy evaluation**: Dask collections integrate with lazy compilation
3. **Scalability**: Handles large datasets efficiently from day one
4. **No simple approaches**: Direct implementation with Dask, no testing shortcuts

**Implementation**: `range()` primitive returns Dask bag, lazy compilation works with Dask collections natively.