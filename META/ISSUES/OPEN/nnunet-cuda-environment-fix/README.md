# nnUNet CUDA Environment Fix

## Issue Description

The nnUNet primitives had a bug where CUDA environment variables were not properly managed, causing:

```
RuntimeError: No CUDA GPUs are available
```

Even when `nvidia-smi` showed GPU availability and PyTorch could see CUDA devices.

## Root Cause

The issue was in `/workspaces/VoxLogicA-2/implementation/python/voxlogica/primitives/nnunet/__init__.py` in the `train_directory` function:

1. When `device='cpu'` was specified, the code set `os.environ['CUDA_VISIBLE_DEVICES'] = ''` globally
2. This affected subsequent subprocess calls that used `_get_nnunet_env()` 
3. The environment modification was applied globally instead of per-subprocess
4. The preprocessing step (`nnUNetv2_plan_and_preprocess`) didn't get the correct environment

## Solution Applied

Fixed the environment handling by:

1. Creating a local copy of the environment with `base_env = _get_nnunet_env()`
2. Applying the CUDA_VISIBLE_DEVICES modification to the local environment copy
3. Using the same `base_env` for both preprocessing and training subprocess calls
4. Avoiding global environment modification

## Files Modified

- `/workspaces/VoxLogicA-2/implementation/python/voxlogica/primitives/nnunet/__init__.py`

## Testing

After the fix, nnUNet training should work properly both with GPU and CPU modes without affecting the global Python environment.

## Date

August 9, 2025
