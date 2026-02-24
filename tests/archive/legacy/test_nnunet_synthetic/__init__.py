"""
nnUNet Synthetic Dataset Test

This test generates synthetic images with squares of varying intensity
and tests nnUNet training/validation workflows in VoxLogicA-2.
"""

from .generate_synthetic_data import SyntheticDatasetGenerator

__all__ = ['SyntheticDatasetGenerator']
