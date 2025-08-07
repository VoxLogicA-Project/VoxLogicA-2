#!/usr/bin/env python3
"""
Synthetic Dataset Generator for nnUNet Testing

This module generates synthetic images containing squares of varying intensity
in grayscale, along with corresponding ground truth masks that select squares
with intensity greater than half the possible range.

The generated dataset can be used to test nnUNet training and validation
workflows in VoxLogicA-2.
"""

import os
import sys
import numpy as np
import SimpleITK as sitk
import random
from pathlib import Path
from typing import Tuple, List, Optional
import argparse


class SyntheticDatasetGenerator:
    """Generator for synthetic medical imaging datasets with controllable parameters"""
    
    def __init__(self, 
                 n_images: int = 20,
                 image_size: Tuple[int, int, int] = (64, 64, 32),
                 intensity_range: Tuple[int, int] = (0, 255),
                 min_squares: int = 2,
                 max_squares: int = 6,
                 min_square_size: int = 8,
                 max_square_size: int = 16,
                 output_dir: str = "/tmp/nnunet_synthetic_data",
                 seed: Optional[int] = 42):
        """
        Initialize the synthetic dataset generator.
        
        Args:
            n_images: Number of images to generate
            image_size: Size of generated images (width, height, depth)
            intensity_range: Min and max intensity values
            min_squares: Minimum number of squares per image
            max_squares: Maximum number of squares per image
            min_square_size: Minimum square size
            max_square_size: Maximum square size
            output_dir: Output directory for generated data
            seed: Random seed for reproducibility
        """
        self.n_images = n_images
        self.image_size = image_size
        self.intensity_range = intensity_range
        self.min_squares = min_squares
        self.max_squares = max_squares
        self.min_square_size = min_square_size
        self.max_square_size = max_square_size
        self.output_dir = Path(output_dir)
        self.seed = seed
        
        # Calculate threshold for ground truth
        self.intensity_threshold = (intensity_range[1] - intensity_range[0]) // 2 + intensity_range[0]
        
        # Set random seed
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)
            
        # Create output directories
        self.setup_directories()
        
    def setup_directories(self):
        """Create necessary output directories"""
        self.images_dir = self.output_dir / "images"
        self.labels_dir = self.output_dir / "labels"
        
        for directory in [self.output_dir, self.images_dir, self.labels_dir]:
            directory.mkdir(parents=True, exist_ok=True)
            
        print(f"Output directories created at: {self.output_dir}")
        
    def generate_square(self, 
                       image: np.ndarray, 
                       mask: np.ndarray,
                       square_id: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generate a single square with random position, size, and intensity.
        
        Args:
            image: Image array to modify
            mask: Ground truth mask to modify
            square_id: ID for the square (used as label value)
            
        Returns:
            Modified image and mask arrays
        """
        # Random square parameters
        square_size = random.randint(self.min_square_size, self.max_square_size)
        intensity = random.randint(self.intensity_range[0], self.intensity_range[1])
        
        # Random position (ensure square fits in image)
        max_x = self.image_size[0] - square_size
        max_y = self.image_size[1] - square_size
        max_z = self.image_size[2] - square_size
        
        if max_x <= 0 or max_y <= 0 or max_z <= 0:
            print(f"Warning: Square size {square_size} too large for image size {self.image_size}")
            return image, mask
            
        x = random.randint(0, max_x)
        y = random.randint(0, max_y)
        z = random.randint(0, max_z)
        
        # Add square to image
        image[x:x+square_size, y:y+square_size, z:z+square_size] = intensity
        
        # Add to ground truth if intensity > threshold
        if intensity > self.intensity_threshold:
            mask[x:x+square_size, y:y+square_size, z:z+square_size] = square_id
            
        return image, mask
        
    def generate_single_image(self, image_id: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generate a single synthetic image with squares and corresponding ground truth.
        
        Args:
            image_id: Unique identifier for the image
            
        Returns:
            Tuple of (image_array, ground_truth_array)
        """
        # Initialize arrays
        image = np.zeros(self.image_size, dtype=np.uint8)
        mask = np.zeros(self.image_size, dtype=np.uint8)  # 0 = background
        
        # Add random background noise
        background_intensity = random.randint(self.intensity_range[0], 
                                            self.intensity_range[0] + 30)
        noise = np.random.normal(background_intensity, 5, self.image_size)
        image = np.clip(noise, self.intensity_range[0], self.intensity_range[1]).astype(np.uint8)
        
        # Generate random number of squares
        n_squares = random.randint(self.min_squares, self.max_squares)
        
        for square_id in range(1, n_squares + 1):  # Start from 1 (0 is background)
            image, mask = self.generate_square(image, mask, square_id)
            
        return image, mask
        
    def save_image_sitk(self, array: np.ndarray, filename: Path, spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
        """
        Save array as SimpleITK image with proper metadata.
        
        Args:
            array: Image array to save
            filename: Output filename
            spacing: Voxel spacing
        """
        # Convert to SimpleITK image
        sitk_image = sitk.GetImageFromArray(array)
        sitk_image.SetSpacing(spacing)
        sitk_image.SetOrigin((0.0, 0.0, 0.0))
        
        # Save image
        sitk.WriteImage(sitk_image, str(filename))
        
    def generate_dataset(self) -> dict:
        """
        Generate the complete synthetic dataset.
        
        Returns:
            Dictionary with dataset information
        """
        print(f"Generating {self.n_images} synthetic images...")
        print(f"Image size: {self.image_size}")
        print(f"Intensity range: {self.intensity_range}")
        print(f"Intensity threshold: {self.intensity_threshold}")
        print(f"Squares per image: {self.min_squares}-{self.max_squares}")
        print(f"Square size range: {self.min_square_size}-{self.max_square_size}")
        
        dataset_info = {
            'n_images': self.n_images,
            'image_size': self.image_size,
            'intensity_range': self.intensity_range,
            'intensity_threshold': self.intensity_threshold,
            'images': [],
            'labels': []
        }
        
        for i in range(self.n_images):
            print(f"Generating image {i+1}/{self.n_images}...")
            
            # Generate image and ground truth
            image, mask = self.generate_single_image(i)
            
            # Create filenames
            image_filename = self.images_dir / f"case_{i:03d}_0000.nii.gz"
            label_filename = self.labels_dir / f"case_{i:03d}.nii.gz"
            
            # Save images
            self.save_image_sitk(image, image_filename)
            self.save_image_sitk(mask, label_filename)
            
            dataset_info['images'].append(str(image_filename))
            dataset_info['labels'].append(str(label_filename))
            
            # Print some statistics
            unique_labels = np.unique(mask)
            high_intensity_pixels = np.sum(image > self.intensity_threshold)
            total_pixels = np.prod(self.image_size)
            
            print(f"  - Unique labels: {unique_labels}")
            print(f"  - High intensity pixels: {high_intensity_pixels}/{total_pixels} "
                  f"({100*high_intensity_pixels/total_pixels:.1f}%)")
                  
        # Save dataset information
        info_file = self.output_dir / "dataset_info.txt"
        with open(info_file, 'w') as f:
            f.write(f"Synthetic Dataset Information\n")
            f.write(f"============================\n\n")
            f.write(f"Number of images: {self.n_images}\n")
            f.write(f"Image size: {self.image_size}\n")
            f.write(f"Intensity range: {self.intensity_range}\n")
            f.write(f"Intensity threshold: {self.intensity_threshold}\n")
            f.write(f"Squares per image: {self.min_squares}-{self.max_squares}\n")
            f.write(f"Square size range: {self.min_square_size}-{self.max_square_size}\n")
            f.write(f"Random seed: {self.seed}\n\n")
            f.write(f"Images directory: {self.images_dir}\n")
            f.write(f"Labels directory: {self.labels_dir}\n")
            
        print(f"\nDataset generation completed!")
        print(f"Images saved in: {self.images_dir}")
        print(f"Labels saved in: {self.labels_dir}")
        print(f"Dataset info saved in: {info_file}")
        
        return dataset_info


def main():
    """Main function for command-line usage"""
    parser = argparse.ArgumentParser(description='Generate synthetic dataset for nnUNet testing')
    parser.add_argument('--n-images', type=int, default=20, 
                       help='Number of images to generate (default: 20)')
    parser.add_argument('--image-size', type=int, nargs=3, default=[64, 64, 32],
                       help='Image size as width height depth (default: 64 64 32)')
    parser.add_argument('--output-dir', type=str, default='/tmp/nnunet_synthetic_data',
                       help='Output directory (default: /tmp/nnunet_synthetic_data)')
    parser.add_argument('--intensity-range', type=int, nargs=2, default=[0, 255],
                       help='Intensity range as min max (default: 0 255)')
    parser.add_argument('--min-squares', type=int, default=2,
                       help='Minimum squares per image (default: 2)')
    parser.add_argument('--max-squares', type=int, default=6,
                       help='Maximum squares per image (default: 6)')
    parser.add_argument('--min-square-size', type=int, default=8,
                       help='Minimum square size (default: 8)')
    parser.add_argument('--max-square-size', type=int, default=16,
                       help='Maximum square size (default: 16)')
    parser.add_argument('--seed', type=int, default=42,
                       help='Random seed for reproducibility (default: 42)')
    
    args = parser.parse_args()
    
    # Create generator
    generator = SyntheticDatasetGenerator(
        n_images=args.n_images,
        image_size=tuple(args.image_size),
        intensity_range=tuple(args.intensity_range),
        min_squares=args.min_squares,
        max_squares=args.max_squares,
        min_square_size=args.min_square_size,
        max_square_size=args.max_square_size,
        output_dir=args.output_dir,
        seed=args.seed
    )
    
    # Generate dataset
    dataset_info = generator.generate_dataset()
    
    return dataset_info


if __name__ == "__main__":
    main()
