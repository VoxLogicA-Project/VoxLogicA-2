# nnUNet CUDA Environment Fix

## Issue Description

The nnUNet primitives in VoxLogicA-2 are failing with CUDA errors even when:
1. NVIDIA GPU is available (verified via `nvidia-smi`)
2. PyTorch with CUDA support is properly installed in the virtual environment
3. CUDA is accessible from within the virtual environment

## Root Cause

The issue occurs because `subprocess.run()` calls in the nnUNet primitives don't properly inherit the virtual environment context. When nnUNet commands like `nnUNetv2_train` and `nnUNetv2_plan_and_preprocess` are executed, they may:

1. Use the system Python instead of the virtual environment Python
2. Not find the correct PyTorch installation with CUDA support
3. Not inherit the proper PATH environment variables

## Error Trace
```
RuntimeError: No CUDA GPUs are available
  File "/workspaces/VoxLogicA-2/.venv/lib/python3.13/site-packages/torch/cuda/__init__.py", line 412, in _lazy_init
    torch._C._cuda_init()
```

## Solution Strategy

1. **Fix subprocess environment inheritance**: Ensure nnUNet command subprocess calls properly inherit the virtual environment
2. **Use absolute paths**: Use absolute paths to nnUNet executables in the virtual environment  
3. **Explicitly pass environment variables**: Pass the correct PATH and PYTHONPATH to subprocess calls
4. **Add environment validation**: Add checks to ensure nnUNet commands run in the correct environment

## Files to Modify

- `/workspaces/VoxLogicA-2/implementation/python/voxlogica/primitives/nnunet/__init__.py`
  - Lines ~314 (nnUNetv2_plan_and_preprocess)
  - Lines ~324 (nnUNetv2_train) 
  - Lines ~121 (nnUNetv2_predict)

## Implementation

The fix involves:
1. Getting the virtual environment's bin directory path
2. Using absolute paths for nnUNet commands
3. Explicitly passing the virtual environment in subprocess.run() calls
