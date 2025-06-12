#!/usr/bin/env python3
"""
Generate a simple test dataset with numeric files for VoxLogicA-2 dataset API testing.
This creates basic text files with numeric values to test the dataset operations
without requiring complex image processing dependencies.
"""

import os
import json
from pathlib import Path
import random


def create_simple_dataset(output_dir: str, num_files: int = 10) -> dict:
    """
    Create a simple dataset of text files containing numeric values.
    
    Args:
        output_dir: Directory to save the files
        num_files: Number of files to create
        
    Returns:
        Dataset metadata dictionary
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating simple dataset with {num_files} files in {output_path}")
    
    dataset_metadata = {
        "dataset_info": {
            "name": "Simple Numeric Dataset",
            "description": "Basic numeric files for VoxLogicA-2 dataset API testing",
            "num_files": num_files,
            "file_type": "text"
        },
        "files": []
    }
    
    for i in range(num_files):
        # Generate random numeric value
        value = random.randint(1, 100)
        
        # Create filename
        filename = f"data_{i:03d}.txt"
        filepath = output_path / filename
        
        # Write the numeric value to file
        with open(filepath, 'w') as f:
            f.write(str(value))
        
        # Add to metadata
        file_metadata = {
            "filename": filename,
            "value": value,
            "file_path": str(filepath)
        }
        dataset_metadata["files"].append(file_metadata)
        
        print(f"  Created {filename} with value {value}")
    
    # Save dataset metadata
    metadata_file = output_path / "dataset_metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(dataset_metadata, f, indent=2)
    
    print(f"Dataset created successfully!")
    print(f"  Total files: {len(dataset_metadata['files'])}")
    print(f"  Metadata file: {metadata_file}")
    
    return dataset_metadata


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate simple numeric dataset for VoxLogicA-2 testing"
    )
    parser.add_argument(
        "output_dir",
        help="Output directory for the dataset"
    )
    parser.add_argument(
        "--num-files", 
        type=int, 
        default=10,
        help="Number of files to create (default: 10)"
    )
    
    args = parser.parse_args()
    
    try:
        create_simple_dataset(args.output_dir, args.num_files)
        print("✅ Simple dataset generation completed successfully!")
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)
"""

import os
import json
from pathlib import Path
import random


def create_simple_dataset(output_dir: str, num_files: int = 10) -> dict:
    """
    Create a simple dataset of text files containing numeric values.
    
    Args:
        output_dir: Directory to save the files
        num_files: Number of files to create
        
    Returns:
        Dataset metadata dictionary
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating simple dataset with {num_files} files in {output_path}")
    
    dataset_metadata = {
        "dataset_info": {
            "name": "Simple Numeric Dataset",
            "description": "Basic numeric files for VoxLogicA-2 dataset API testing",
            "num_files": num_files,
            "file_type": "text"
        },
        "files": []
    }
    
    for i in range(num_files):
        # Generate random numeric value
        value = random.randint(1, 100)
        
        # Create filename
        filename = f"data_{i:03d}.txt"
        filepath = output_path / filename
        
        # Write the numeric value to file
        with open(filepath, 'w') as f:
            f.write(str(value))
        
        # Add to metadata
        file_metadata = {
            "filename": filename,
            "value": value,
            "file_path": str(filepath)
        }
        dataset_metadata["files"].append(file_metadata)
        
        print(f"  Created {filename} with value {value}")
    
    # Save dataset metadata
    metadata_file = output_path / "dataset_metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(dataset_metadata, f, indent=2)
    
    print(f"Dataset created successfully!")
    print(f"  Total files: {len(dataset_metadata['files'])}")
    print(f"  Metadata file: {metadata_file}")
    
    return dataset_metadata


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate simple numeric dataset for VoxLogicA-2 testing"
    )
    parser.add_argument(
        "output_dir",
        help="Output directory for the dataset"
    )
    parser.add_argument(
        "--num-files", 
        type=int, 
        default=10,
        help="Number of files to create (default: 10)"
    )
    
    args = parser.parse_args()
    
    try:
        create_simple_dataset(args.output_dir, args.num_files)
        print("✅ Simple dataset generation completed successfully!")
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)
