# nnUNetv2 Integration Notes

This project now declares `nnunetv2` as a dependency. nnUNetv2 in turn requires `torch`.

Because official PyTorch wheels may lag behind the latest CPython release, the base `requirements.txt` includes a conditional marker that only installs torch on Python versions < 3.13.

If you are on Python 3.13 and need nnUNet functionality:
1. Recreate the virtual environment with Python 3.11 or 3.12.
2. Reinstall dependencies: `pip install -r implementation/python/requirements.txt`
3. (Optional) Manually install a compatible torch build if/when available for 3.13.

Minimal CPU-only installation example (after switching to Python 3.12):
```
pip install --upgrade pip wheel setuptools
pip install -r implementation/python/requirements.txt
```

GPU users should follow the official PyTorch installation selector for their CUDA toolkit.
