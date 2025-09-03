# Auto-inserted by devcontainer to disable Triton and torch.compile features
import os
import sys

# Prevent dynamic compilation features in torch
os.environ.setdefault('TORCH_COMPILE_DISABLE', '1')
os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')
os.environ.setdefault('TORCH_DYNAMO_DISABLE', '1')
os.environ.setdefault('PYTORCH_DISABLE_CUDNN_COMPILATION', '1')

# Create a dummy triton module to avoid importing the real one
class _DummyTriton:
    __all__ = []
    def __getattr__(self, name):
        raise ImportError('triton is disabled in this environment')

sys.modules['triton'] = _DummyTriton()
sys.modules['torch.utils._triton'] = _DummyTriton()
