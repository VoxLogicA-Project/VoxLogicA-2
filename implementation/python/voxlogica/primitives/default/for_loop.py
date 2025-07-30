"""
For-loop primitive for unified execution model.

This primitive implements for-loop semantics by dynamically expanding 
the workplan during execution - no pre-computation during graph construction.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

def execute(**kwargs) -> List[Any]:
    """
    Execute a for-loop by dynamically expanding the workplan.
    
    This is a unified execution model where:
    1. The range is iterated at execution time (not graph construction time)
    2. For each iteration, a new operation is dynamically added to the workplan
    3. All operations execute through the same unified mechanism
    4. No pre-computation - pure lazy evaluation
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected:
                 - '0': The iterable (e.g., range result [1,2,3,...])
                 - 'closure': The closure containing the loop body expression
    
    Returns:
        List of results from applying the closure to each iteration value
    """
    iterable = kwargs["0"]
    closure = kwargs["closure"]
    
    # Import here to avoid circular imports
    from voxlogica.reducer import reduce_expression, ConstantValue, OperationVal
    from voxlogica.execution import get_execution_engine
    import dask.bag as db
    
    logger.info(f"for_loop primitive called with iterable type: {type(iterable)}")
    
    # Handle Dask bag by converting to list for iteration
    if hasattr(iterable, 'compute') and hasattr(iterable, 'npartitions'):
        # This is a Dask bag (more robust detection)
        logger.info(f"Converting Dask bag to list for for-loop iteration")
        try:
            iterable_list = iterable.compute()
            logger.info(f"Successfully converted Dask bag to list with {len(iterable_list)} elements")
        except Exception as e:
            logger.error(f"Failed to compute Dask bag: {e}")
            raise
    elif isinstance(iterable, db.Bag):
        # Fallback isinstance check
        logger.info(f"Converting Dask bag (isinstance) to list for for-loop iteration")
        try:
            iterable_list = iterable.compute()
            logger.info(f"Successfully converted Dask bag to list with {len(iterable_list)} elements")
        except Exception as e:
            logger.error(f"Failed to compute Dask bag: {e}")
            raise
    else:
        # Regular iterable - check if we can safely call len()
        logger.info(f"Processing non-Dask iterable of type: {type(iterable)}")
        try:
            iterable_list = iterable
            list_length = len(iterable_list)
            logger.info(f"Using direct iterable with {list_length} elements")
        except TypeError as e:
            logger.error(f"Cannot get length of iterable type {type(iterable)}: {e}")
            # Try to convert to list if len() fails
            try:
                iterable_list = list(iterable)
                logger.info(f"Converted iterable to list with {len(iterable_list)} elements")
            except Exception as convert_error:
                logger.error(f"Failed to convert iterable to list: {convert_error}")
                raise ValueError(f"Cannot process iterable of type {type(iterable)}: {e}") from e
    
    results = []
    engine = get_execution_engine()
    operation_ids = []
    
    # Phase 1: Create all operations and add them to the workplan
    for i, value in enumerate(iterable_list):
        logger.debug(f"For-loop iteration {i}: processing value {value}")
        
        # Use the closure's logic to get the operation ID with proper deduplication
        # This replicates the logic from ClosureValue.__call__ but only gets the operation ID
        try:
            # Check if the loop variable is actually referenced in the expression
            referenced_vars = closure._get_referenced_variables(closure.expression)
            variable_is_referenced = closure.variable in referenced_vars
            
            if variable_is_referenced:
                # Create a binding for the loop variable (similar to ClosureValue.__call__)
                try:
                    # Try to add the value as a constant node in the workplan
                    value_node = ConstantValue(value=value)
                    value_id = closure.workplan.add_node(value_node)
                    value_dval = OperationVal(value_id)
                    logger.debug(f"Added constant node for iteration value: {value}")
                except (TypeError, ValueError):
                    # Value is not serializable - use temporary storage
                    import uuid
                    temp_id = f"temp_{uuid.uuid4().hex[:16]}"
                    engine.storage._memory_cache[temp_id] = value
                    value_dval = OperationVal(temp_id)
                    logger.debug(f"Used temporary storage for non-serializable value")
                
                # Create environment with the variable bound to this value
                env_with_binding = closure.environment.bind(closure.variable, value_dval)
            else:
                # Loop variable is not referenced, so don't create a binding for it
                # This optimizes loop-invariant expressions
                logger.debug(f"Loop variable '{closure.variable}' not referenced, using optimization")
                env_with_binding = closure.environment
            
            # Check expression cache before reducing (using the closure's cache)
            cache_key = closure._create_cache_key(env_with_binding)
            if cache_key in closure._expression_cache:
                result_id = closure._expression_cache[cache_key]
                logger.debug(f"Retrieved operation ID from cache: {result_id[:8]}...")
            else:
                # Reduce the expression with this environment, using the existing workplan
                # This builds the DAG lazily without executing anything
                result_id = reduce_expression(env_with_binding, closure.workplan, closure.expression)
                
                # Cache the result for future iterations
                closure._expression_cache[cache_key] = result_id
                logger.debug(f"Reduced expression to new operation ID: {result_id[:8]}...")
            
            operation_ids.append(result_id)
            
        except Exception as e:
            logger.error(f"Error in iteration {i}: {e}")
            # Fallback to old approach
            value_node = ConstantValue(value=value)
            value_id = closure.workplan.add_node(value_node)
            value_dval = OperationVal(value_id)
            env_with_binding = closure.environment.bind(closure.variable, value_dval)
            result_id = reduce_expression(env_with_binding, closure.workplan, closure.expression)
            operation_ids.append(result_id)
        
        # Check if this operation ID was seen in previous iterations
        if operation_ids[-1] in operation_ids[:-1]:  # Check all previous iterations
            logger.info(f"✅ DEDUPLICATION SUCCESS: Iteration {i} reused existing operation {operation_ids[-1][:8]}...")
        else:
            logger.info(f"❌ DEDUPLICATION MISS: Iteration {i} created new operation {operation_ids[-1][:8]}...")
        if result_id in operation_ids[:-1]:
            logger.info(f"✅ DEDUPLICATION SUCCESS: Iteration {i} reused existing operation {result_id[:8]}...")
        else:
            logger.info(f"❌ DEDUPLICATION MISS: Iteration {i} created new operation {result_id[:8]}...")
        
        logger.debug(f"Created operation {result_id[:8]}... for iteration {i}")
    
    # Phase 2: Execute all operations through the unified execution model
    # Create a temporary workplan containing just the operations we need to execute
    from voxlogica.reducer import WorkPlan, Goal
    from voxlogica.execution import ExecutionSession
    
    temp_workplan = WorkPlan()
    temp_workplan.nodes = closure.workplan.nodes.copy()  # Copy all nodes (constants, operations, etc.)
    
    # Add a dummy goal for each operation we need to execute
    # This ensures the execution system will compute these operations
    for i, result_id in enumerate(operation_ids):
        if not engine.storage.exists(result_id):
            # Add a dummy goal to trigger execution of this operation
            temp_workplan.add_goal("print", result_id, f"iteration_{i}")
    
    # Execute the temporary workplan if there are any operations to compute
    if temp_workplan.goals:
        logger.info(f"Executing {len(temp_workplan.goals)} operations through unified execution model")
        
        # Create an execution session for the temporary workplan
        import uuid
        temp_execution_id = f"for_loop_{uuid.uuid4().hex[:8]}"
        
        # Debug: Check what's in the temporary workplan
        logger.debug(f"Temporary workplan has {len(temp_workplan.nodes)} nodes")
        for result_id in operation_ids:
            if result_id in temp_workplan.nodes:
                node = temp_workplan.nodes[result_id]
                from voxlogica.reducer import Operation
                if isinstance(node, Operation):
                    logger.debug(f"Operation {result_id[:8]}... operator: {node.operator}, arguments: {node.arguments}")
                else:
                    logger.debug(f"Node {result_id[:8]}... type: {type(node).__name__}")
        
        
        session = ExecutionSession(temp_execution_id, temp_workplan, engine.storage, engine.primitives)
        
        try:
            # Execute the workplan (this will compute all the operations)
            completed, failed = session.execute()
            
            if failed:
                error_details = "; ".join([f"{op_id[:8]}...: {error}" for op_id, error in failed.items()])
                raise Exception(f"For-loop execution failed: {error_details}")
                
            logger.info(f"Successfully executed {len(completed)} operations")
            
        except Exception as e:
            logger.error(f"Failed to execute for-loop operations: {e}")
            raise Exception(f"For-loop execution failed: {e}") from e
    
    # Phase 3: Collect all results
    for i, result_id in enumerate(operation_ids):
        if engine.storage.exists(result_id):
            # Get the computed result
            result = engine.storage.retrieve(result_id)
            logger.debug(f"Retrieved result for iteration {i}: {result}")
        else:
            # This should not happen if execution was successful
            logger.error(f"Operation {result_id[:8]}... not found in storage after execution")
            result = f"missing_operation_{result_id[:8]}"
        
        results.append(result)
    
    logger.info(f"For-loop completed: {len(results)} results")
    return results

# Register the primitive with metadata
PRIMITIVE_METADATA = {
    "name": "for_loop",
    "description": "Execute a for-loop with unified execution model",
    "function": execute,
    "return_type": "list",
    "arguments": {
        "0": "iterable",
        "closure": "closure"
    }
}
