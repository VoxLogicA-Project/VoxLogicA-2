"""
Dataset mapping with dynamic VoxLogicA function compilation
"""
import dask.bag as db
from dask.delayed import delayed
from typing import Any

def _compile_and_apply_element(element: Any, function_name: str, environment) -> Any:
    """Compile and apply function to single dataset element (module-level for Dask pickling)"""
    from voxlogica.dynamic_compilation import compile_function_with_element, merge_workplans
    from voxlogica.reducer import WorkPlan, ConstantValue
    from voxlogica.execution import get_execution_context, ExecutionEngine
    from voxlogica.storage import get_storage
    from voxlogica.execution import PrimitivesLoader
    
    # Element becomes a constant in the workplan - use existing CBA ID computation
    element_workplan = WorkPlan()
    element_cba_id = element_workplan.add_node(ConstantValue(element))
    
    # Dynamically compile f(element_cba_id) using reducer's environment
    dynamic_workplan, result_id = compile_function_with_element(
        environment,
        function_name, 
        element_cba_id
    )
    
    # Merge element constant into dynamic workplan
    merged_workplan = merge_workplans(element_workplan, dynamic_workplan)
    
    # Execute the compiled function using temporary engine with environment
    temp_engine = ExecutionEngine(
        storage_backend=get_storage(),
        primitives_loader=PrimitivesLoader(),
        environment=environment
    )
    execution_result = temp_engine.execute_workplan(merged_workplan)
    
    if not execution_result.success:
        raise RuntimeError(f"Dynamic compilation failed for element: {execution_result.failed_operations}")
    
    # Retrieve result from storage using result_id
    return temp_engine.storage.retrieve(result_id)

def _extract_function_name(function_ref) -> str:
    """
    Extract function name from function reference (string literal or function symbol)
    
    Args:
        function_ref: Either a string literal (backward compatibility) or function symbol reference
        
    Returns:
        str: Function name for dynamic compilation
        
    Raises:
        TypeError: If function_ref is not a valid function reference
    """
    # Case 1: String literal (backward compatibility)
    if isinstance(function_ref, str):
        return function_ref
    
    # Case 2: Function symbol - need to access storage to get the operation details
    # This case occurs when a function symbol like 'add_ten' is passed without quotes
    # The execution engine will have stored this as an operation in storage
    try:
        from voxlogica.execution import get_execution_engine
        engine = get_execution_engine()
        
        # For function symbols, function_ref should be a storage key/operation_id
        # We need to retrieve the operation and extract the operator name
        if hasattr(engine, 'storage') and engine.storage:
            # Try to retrieve the operation from storage
            try:
                operation_data = engine.storage.retrieve(function_ref)
                if operation_data is not None:
                    # If we got operation data, check if it represents a function symbol
                    if hasattr(operation_data, 'operator'):
                        return str(operation_data.operator)
                    elif isinstance(operation_data, str):
                        return operation_data
                    else:
                        # If operation_data is not what we expect, fall back to string conversion
                        return str(function_ref)
                else:
                    # If retrieval returned None, convert function_ref to string
                    return str(function_ref)
            except Exception:
                # If storage retrieval fails, try to extract from function_ref directly
                return str(function_ref)
        else:
            # No storage available, convert directly to string
            return str(function_ref)
            
    except Exception:
        # If anything fails, fall back to string conversion
        return str(function_ref)


def execute(**kwargs) -> db.Bag:
    """
    Apply VoxLogicA function to each dataset element with dynamic compilation
    
    Args:
        **kwargs: VoxLogicA argument convention:
            '0': dataset (dask.bag.Bag) - Dataset to transform
            '1': function_ref (str|function_symbol) - Function name or function symbol to apply
            
    Returns:
        dask.bag.Bag: Transformed dataset with function applied to each element
    """
    dataset = kwargs['0']  # Follow VoxLogicA argument convention: dataset first
    function_ref = kwargs['1']  # Function reference second (string or function symbol)
    
    if not hasattr(dataset, 'map'):
        raise TypeError(f"Expected Dask bag, got {type(dataset)}")
    
    # Extract function name from function reference
    function_name = _extract_function_name(function_ref)
    
    # Get execution context for dynamic compilation
    from voxlogica.execution import get_execution_context, get_execution_environment
    
    # Try to get environment from current execution context first
    environment = get_execution_environment()
    
    if not environment:
        # Fallback to global execution engine (for backwards compatibility)
        from voxlogica.execution import get_execution_engine
        engine = get_execution_engine()
        if not engine.environment:
            raise RuntimeError("Dynamic compilation requires environment - ensure ExecutionEngine has environment set")
        environment = engine.environment

    # Apply dynamic compilation to each element using module-level function
    from functools import partial
    map_func = partial(_compile_and_apply_element, function_name=function_name, environment=environment)
    return dataset.map(map_func)
