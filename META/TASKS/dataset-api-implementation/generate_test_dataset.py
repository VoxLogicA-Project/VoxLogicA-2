#!/usr/bin/env python3
"""
Generate fictional rotated medical images dataset for VoxLogicA-2 dataset API testing.

This script creates synthetic .nii.gz files with realistic metadata to simulate
a medical imaging dataset where images have been rotated at different angles.
"""

import numpy as np
import nibabel as nib
import os
import json
from pathlib import Path
from typing import Dict, List, Tuple
import argparse


def create_synthetic_brain_image(shape: Tuple[int, int, int] = (64, 64, 64)) -> np.ndarray:
    """
    Create a synthetic 3D brain-like image with various structures.
    
    Args:
        shape: 3D shape of the image (x, y, z)
        
    Returns:
        3D numpy array representing the synthetic brain image
    """
    x, y, z = shape
    image = np.zeros(shape, dtype=np.float32)
    
    # Create coordinate grids
    xx, yy, zz = np.meshgrid(
        np.linspace(-1, 1, x),
        np.linspace(-1, 1, y), 
        np.linspace(-1, 1, z),
        indexing='ij'
    )
    
    # Create brain-like structures
    # Outer brain boundary (ellipsoid)
    brain_mask = (xx**2 + yy**2 + (zz*1.5)**2) < 0.8
    image[brain_mask] = 0.3
    
    # Gray matter (outer shell)
    gray_matter = brain_mask & ((xx**2 + yy**2 + (zz*1.5)**2) > 0.5)
    image[gray_matter] = 0.6
    
    # White matter (inner core)
    white_matter = (xx**2 + yy**2 + (zz*1.8)**2) < 0.4
    image[white_matter] = 0.9
    
    # Ventricles (fluid-filled cavities)
    ventricle_left = ((xx + 0.2)**2 + (yy + 0.1)**2 + (zz*2)**2) < 0.15
    ventricle_right = ((xx - 0.2)**2 + (yy + 0.1)**2 + (zz*2)**2) < 0.15
    image[ventricle_left | ventricle_right] = 0.1
    
    # Add some noise for realism
    noise = np.random.normal(0, 0.05, shape)
    image = np.clip(image + noise, 0, 1)
    
    return image


def rotate_image_3d(image: np.ndarray, rotation_angle: float, axis: int = 2) -> np.ndarray:
    """
    Rotate a 3D image around a specified axis.
    
    Args:
        image: 3D numpy array
        rotation_angle: Rotation angle in degrees
        axis: Axis to rotate around (0=x, 1=y, 2=z)
        
    Returns:
        Rotated 3D image
    """
    from scipy.ndimage import rotate
    return rotate(image, rotation_angle, axes=(0, 1) if axis == 2 else (0, 2) if axis == 1 else (1, 2), 
                  reshape=False, order=1, mode='constant', cval=0)


def create_nifti_header(rotation_angle: float, patient_id: str) -> nib.Nifti1Header:
    """
    Create a realistic NIfTI header with rotation metadata.
    
    Args:
        rotation_angle: Rotation angle applied to the image
        patient_id: Patient identifier
        
    Returns:
        NIfTI header object
    """
    header = nib.Nifti1Header()
    
    # Set voxel dimensions (1mm isotropic)
    header.set_zooms([1.0, 1.0, 1.0])
    
    # Set data type
    header.set_data_dtype(np.float32)
    
    # Add rotation information in the description field
    description = f"PATIENT:{patient_id};ROTATION:{rotation_angle:.1f}deg"
    header['descrip'] = description.encode('utf-8')[:79]  # NIfTI limit is 80 chars
    
    return header


def generate_dataset(
    output_dir: str,
    num_patients: int = 10,
    rotations_per_patient: int = 5,
    image_shape: Tuple[int, int, int] = (64, 64, 64)
) -> Dict[str, List[Dict]]:
    """
    Generate a complete dataset of rotated medical images.
    
    Args:
        output_dir: Directory to save the dataset
        num_patients: Number of different patients/base images
        rotations_per_patient: Number of rotated versions per patient
        image_shape: 3D shape of each image
        
    Returns:
        Dataset metadata dictionary
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    dataset_metadata = {
        "dataset_info": {
            "name": "Synthetic Rotated Brain Images",
            "description": "Fictional dataset of rotated medical brain images for VoxLogicA-2 testing",
            "num_patients": num_patients,
            "rotations_per_patient": rotations_per_patient,
            "image_shape": list(image_shape),
            "voxel_size": [1.0, 1.0, 1.0],
            "units": "mm"
        },
        "images": []
    }
    
    print(f"Generating {num_patients} patients with {rotations_per_patient} rotations each...")
    
    for patient_id in range(1, num_patients + 1):
        print(f"  Patient {patient_id:02d}...")
        
        # Generate base image for this patient
        base_image = create_synthetic_brain_image(image_shape)
        
        # Create rotated versions
        rotation_angles = np.linspace(0, 360, rotations_per_patient, endpoint=False)
        
        for i, angle in enumerate(rotation_angles):
            # Rotate the image
            if angle == 0:
                rotated_image = base_image
            else:
                rotated_image = rotate_image_3d(base_image, angle)
            
            # Create filename
            filename = f"patient_{patient_id:02d}_rotation_{angle:03.0f}deg.nii.gz"
            filepath = output_path / filename
            
            # Create NIfTI header with metadata
            header = create_nifti_header(angle, f"PAT{patient_id:02d}")
            
            # Create NIfTI image and save
            nifti_img = nib.Nifti1Image(rotated_image, affine=np.eye(4), header=header)
            nib.save(nifti_img, str(filepath))
            
            # Add to metadata
            image_metadata = {
                "filename": filename,
                "patient_id": f"PAT{patient_id:02d}",
                "rotation_angle": float(angle),
                "file_path": str(filepath),
                "image_shape": list(image_shape),
                "data_type": "float32"
            }
            dataset_metadata["images"].append(image_metadata)
    
    # Save dataset metadata
    metadata_file = output_path / "dataset_metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(dataset_metadata, f, indent=2)
    
    print(f"Dataset generated successfully!")
    print(f"  Total images: {len(dataset_metadata['images'])}")
    print(f"  Output directory: {output_path}")
    print(f"  Metadata file: {metadata_file}")
    
    return dataset_metadata


def main():
    """Main function with command line interface."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic rotated medical images dataset for VoxLogicA-2 testing"
    )
    parser.add_argument(
        "output_dir",
        help="Output directory for the dataset"
    )
    parser.add_argument(
        "--num-patients", 
        type=int, 
        default=10,
        help="Number of different patients/base images (default: 10)"
    )
    parser.add_argument(
        "--rotations-per-patient", 
        type=int, 
        default=5,
        help="Number of rotated versions per patient (default: 5)"
    )
    parser.add_argument(
        "--image-size", 
        type=int, 
        default=64,
        help="Size of cubic images (default: 64x64x64)"
    )
    parser.add_argument(
        "--quick", 
        action="store_true",
        help="Generate a small dataset quickly (3 patients, 3 rotations)"
    )
    
    args = parser.parse_args()
    
    if args.quick:
        num_patients = 3
        rotations_per_patient = 3
        image_size = 32
        print("Quick mode: generating small dataset...")
    else:
        num_patients = args.num_patients
        rotations_per_patient = args.rotations_per_patient
        image_size = args.image_size
    
    image_shape = (image_size, image_size, image_size)
    
    try:
        generate_dataset(
            args.output_dir,
            num_patients=num_patients,
            rotations_per_patient=rotations_per_patient,
            image_shape=image_shape
        )
        print("\n✅ Dataset generation completed successfully!")
        
    except ImportError as e:
        if "nibabel" in str(e) or "scipy" in str(e):
            print("❌ Error: Required dependencies not installed.")
            print("Please install them with:")
            print("  pip install nibabel scipy numpy")
        else:
            raise
    except Exception as e:
        print(f"❌ Error generating dataset: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
