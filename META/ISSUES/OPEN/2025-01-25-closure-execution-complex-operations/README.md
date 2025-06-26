# 2025-01-25-closure-execution-complex-operations

## Issue: Complex Operations in Closures Not Resolving Correctly - RESOLVED ✅

### Problem
While basic closure execution worked initially (simple values and direct function calls), complex operations within closures were failing due to argument mapping issues.

### Root Cause Identified
The `_execute_operation_directly` method in `ClosureValue` was not properly mapping numeric argument keys to semantic argument names expected by primitive functions.

When function calls are reduced, arguments are stored with numeric string keys (`'0'`, `'1'`, etc.), but primitive functions expect semantic names like `left` and `right` for binary operators.

### Solution Implemented
Added `_map_arguments_to_semantic_names` method to the `ClosureValue` class (copied from the execution engine) that maps:
- Numeric keys (`'0'`, `'1'`) to semantic names (`left`, `right`) for binary operators like addition
- This ensures primitives receive correctly named arguments

### Code Changes
1. **Added method to `ClosureValue` class** in `/implementation/python/voxlogica/reducer.py`:
   ```python
   def _map_arguments_to_semantic_names(self, operator: Any, args: Dict[str, Any]) -> Dict[str, Any]:
       """Map numeric argument keys to semantic names based on operator."""
       operator_str = str(operator).lower()
       
       # Binary operators mapping
       if operator_str in ['+', 'add', 'addition', '-', 'sub', 'subtract', 
                          '*', 'mul', 'multiply', '/', 'div', 'divide']:
           if '0' in args and '1' in args:
               return {'left': args['0'], 'right': args['1']}
       
       return args
   ```

2. **Modified `_execute_operation_directly`** to use semantic mapping before executing primitives:
   ```python
   # Map numeric argument keys to semantic names for primitives
   resolved_args = self._map_arguments_to_semantic_names(operation.operator, resolved_args)
   
   # Execute the primitive
   result = primitive_func(**resolved_args)
   ```

### Test Results
All test cases now work correctly:

- **Simple test** (`test_debug_simple.imgql`): ✅ Working
- **Full test** (`test_simpleitk.imgql`): ✅ Working - prints actual computed values instead of operation IDs

Example outputs:
- Before: `data2=['3117b4e1c49d4a119aae51799351b96e9a0e0dbb4036721a694f8881cffa9b54', ...]`
- After: `data2=[(0.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, 1.0)]`

### Status: RESOLVED ✅
Date: 2025-01-25
Time: ~1 hour

The VoxLogicA-2 interpreter now correctly executes nested for-loops and SimpleITK primitives, printing computed values instead of operation IDs, with proper lazy workplan (DAG) generation and evaluation using the storage system.
