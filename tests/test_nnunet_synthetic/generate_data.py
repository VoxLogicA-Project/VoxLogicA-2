#!/usr/bin/env python3

import sys
import os
sys.path.append('/Users/vincenzo/data/local/repos/VoxLogicA-2/tests/test_nnunet_synthetic')

from generate_synthetic_data import SyntheticDatasetGenerator

# Generate the full synthetic dataset with 20 images
generator = SyntheticDatasetGenerator(
    n_images=20,
    output_dir="/tmp/nnunet_synthetic_data"
)

print("Generating 20 synthetic images...")
generator.generate_dataset()
print("Dataset generation completed!")

# Verify the generated files
import os
images_dir = "/tmp/nnunet_synthetic_data/images"
labels_dir = "/tmp/nnunet_synthetic_data/labels"

if os.path.exists(images_dir):
    image_files = sorted([f for f in os.listdir(images_dir) if f.endswith('.nii.gz')])
    print(f"Generated {len(image_files)} image files:")
    for f in image_files[:5]:  # Show first 5
        print(f"  {f}")
    if len(image_files) > 5:
        print(f"  ... and {len(image_files) - 5} more")

if os.path.exists(labels_dir):
    label_files = sorted([f for f in os.listdir(labels_dir) if f.endswith('.nii.gz')])
    print(f"Generated {len(label_files)} label files:")
    for f in label_files[:5]:  # Show first 5
        print(f"  {f}")
    if len(label_files) > 5:
        print(f"  ... and {len(label_files) - 5} more")