"""
Pure functional dynamic compilation for dataset operations
"""
from typing import Dict, Tuple
from voxlogica.reducer import WorkPlan, Environment, FunctionVal, OperationVal, reduce_expression, NodeId, ConstantValue

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
