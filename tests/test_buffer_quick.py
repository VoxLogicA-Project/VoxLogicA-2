import sys
import os
sys.path.insert(0, '/Users/vincenzo/data/local/repos/VoxLogicA-2/implementation/python')

# Test import
try:
    from voxlogica.buffer_allocation import print_buffer_assignment, allocate_buffers
    print('✅ Import successful: print_buffer_assignment and allocate_buffers')
except Exception as e:
    print(f'❌ Import failed: {e}')
    sys.exit(1)

# Test basic functionality
try:
    from voxlogica.reducer import WorkPlan
    
    workplan = WorkPlan()
    op_a = workplan.add_operation('load', {})
    op_b = workplan.add_operation('process', {'input': op_a})
    workplan.add_goal(op_b)
    
    type_assignment = {op_a: 'tensor', op_b: 'tensor'}
    allocation = allocate_buffers(workplan, type_assignment)
    
    print(f'✅ Algorithm works: {allocation}')
    print(f'   Buffers used: {len(set(allocation.values()))}')
    
    # Test print function
    def type_func(op_id):
        return type_assignment[op_id]
    
    print('✅ Print function test:')
    print_buffer_assignment(workplan, allocation, type_func)
    
except Exception as e:
    print(f'❌ Algorithm test failed: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

print('🎉 All tests passed!')