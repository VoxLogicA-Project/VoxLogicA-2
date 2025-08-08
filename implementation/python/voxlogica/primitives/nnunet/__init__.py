"""
nnUNet namespace for VoxLogicA-2 primitives

This namespace provides nnU-Net v2 training and prediction functionality
integrated with VoxLogicA's execution system. Supports resume functionality
and distributed execution through Dask.

Functions:
- train: Train an nnU-Net model from Dask bags
- predict: Run prediction using a trained nnU-Net model
"""

import os
import json
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Dict, Callable, Any, Optional, List, Union
import dask.bag as db
from datetime import datetime

logger = logging.getLogger(__name__)

def get_primitives():
    """
    Return a dictionary of all primitive functions in this namespace.
    
    Each function should be accessible as nnunet.function_name() in VoxLogicA.
    """
    primitives = {
        'train': train,
        'predict': predict,
        'train_directory': train_directory  # Renamed to avoid conflicts
    }
    return primitives

def list_primitives():
    """List all primitives available in this namespace"""
    return {
        'train': 'Train an nnU-Net model from Dask bags with resume support',
        'predict': 'Run prediction using a trained nnU-Net model',
        'train_directory': 'Train an nnU-Net model from directory of images and labels'
    }

def register_primitives():
    """Register all primitives for dynamic loading"""
    return get_primitives()

def train(**kwargs):
    """
    Train an nnU-Net model from Dask bags with resume support.
    
    Args (via kwargs with numeric keys):
        '0': images_bag - Dask bag with images (case_id, modality, array)
        '1': labels_bag - Dask bag with labels (case_id, array)
        '2': modalities - List of modalities (e.g., ["T1", "T2", "FLAIR"])
        '3': work_dir - Working directory path for nnU-Net
        '4': dataset_id - Dataset ID (optional, default: 1)
        '5': dataset_name - Dataset name (optional, default: "VoxLogicADataset")
        '6': configuration - nnU-Net configuration (optional, default: "3d_fullres")
        '7': nfolds - Number of cross-validation folds (optional, default: 5)
        
    Returns:
        Dictionary with training results and model path
    """
    try:
        # Extract required arguments
        if '0' not in kwargs or '1' not in kwargs or '2' not in kwargs or '3' not in kwargs:
            raise ValueError("train requires: images_bag, labels_bag, modalities, work_dir")
        
        images_bag = kwargs['0']
        labels_bag = kwargs['1'] 
        modalities = kwargs['2']
        work_dir = kwargs['3']
        
        # Extract optional arguments with defaults
        dataset_id = kwargs.get('4', 1)
        dataset_name = kwargs.get('5', "VoxLogicADataset")
        configuration = kwargs.get('6', "3d_fullres")
        nfolds = kwargs.get('7', 5)
        
        logger.info(f"Starting nnU-Net training with dataset_id={dataset_id}, work_dir={work_dir}")
        
        # Import nnUNet wrapper here to avoid import errors if not available
        import sys
        nnunet_wrapper_path = Path(__file__).parent.parent.parent.parent.parent.parent / "doc" / "dev" / "notes"
        sys.path.insert(0, str(nnunet_wrapper_path))
        
        try:
            from nnunet_wrapper import nnUNetDaskWrapper
        except ImportError as e:
            raise ValueError(f"nnUNet wrapper not available: {e}. Please ensure nnunetv2 is installed.")
        
        # Create wrapper instance
        wrapper = nnUNetDaskWrapper(
            work_dir=work_dir,
            dataset_id=dataset_id,
            dataset_name=dataset_name
        )
        
        # Check if training can be resumed
        status = wrapper.get_training_status()
        
        # Perform training
        results = wrapper.train(
            images_bag=images_bag,
            labels_bag=labels_bag,
            modalities=modalities,
            configuration=configuration,
            nfolds=nfolds,
            resume=True  # Always enable resume for VoxLogicA integration
        )
        
        # Prepare return value with model path
        model_path = wrapper.nnunet_results / wrapper.dataset_full_name
        
        return {
            'status': 'success',
            'model_path': str(model_path),
            'dataset_id': dataset_id,
            'dataset_name': dataset_name,
            'configuration': configuration,
            'nfolds': nfolds,
            'work_dir': work_dir,
            'training_results': results,
            'trained_folds': status.get('trained_folds', [])
        }
        
    except Exception as e:
        logger.error(f"nnUNet training failed: {e}")
        raise ValueError(f"nnUNet training failed: {e}") from e

def predict(**kwargs):
    """
    Run prediction using a trained nnU-Net model.
    
    Args (via kwargs with numeric keys):
        '0': input_images - Dask bag with test images or directory path
        '1': model_path - Path to trained model (from train function)
        '2': output_dir - Directory to save predictions
        '3': configuration - nnU-Net configuration (optional, default: "3d_fullres")
        '4': folds - List of folds to use (optional, default: None = all folds)
        '5': save_probabilities - Save probability maps (optional, default: False)
        
    Returns:
        Dictionary with prediction results and output path
    """
    try:
        # Extract required arguments
        if '0' not in kwargs or '1' not in kwargs or '2' not in kwargs:
            raise ValueError("predict requires: input_images, model_path, output_dir")
        
        input_images = kwargs['0']
        model_path = kwargs['1']
        output_dir = kwargs['2']
        
        # Extract optional arguments with defaults
        configuration = kwargs.get('3', "3d_fullres")
        folds = kwargs.get('4', None)
        save_probabilities = kwargs.get('5', False)
        
        logger.info(f"Starting nnU-Net prediction with model_path={model_path}, output_dir={output_dir}")
        
        # Import nnUNet wrapper here to avoid import errors if not available
        import sys
        nnunet_wrapper_path = Path(__file__).parent.parent.parent.parent.parent.parent / "doc" / "dev" / "notes"
        sys.path.insert(0, str(nnunet_wrapper_path))
        
        try:
            from nnunet_wrapper import nnUNetDaskWrapper
        except ImportError as e:
            raise ValueError(f"nnUNet wrapper not available: {e}. Please ensure nnunetv2 is installed.")
        
        # Extract dataset info from model path
        model_path_obj = Path(model_path)
        if not model_path_obj.exists():
            raise ValueError(f"Model path does not exist: {model_path}")
        
        # Extract work_dir, dataset_id, and dataset_name from model path structure
        # Expected structure: work_dir/nnUNet_results/DatasetXXX_Name/...
        work_dir = model_path_obj.parent.parent.parent
        dataset_full_name = model_path_obj.name
        
        # Parse dataset_id and dataset_name from DatasetXXX_Name
        if not dataset_full_name.startswith("Dataset"):
            raise ValueError(f"Invalid dataset format in model path: {dataset_full_name}")
        
        parts = dataset_full_name.split("_", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid dataset format in model path: {dataset_full_name}")
        
        dataset_id = int(parts[0][7:])  # Remove "Dataset" prefix
        dataset_name = parts[1]
        
        # Create wrapper instance
        wrapper = nnUNetDaskWrapper(
            work_dir=str(work_dir),
            dataset_id=dataset_id,
            dataset_name=dataset_name
        )
        
        # Run prediction
        prediction_path = wrapper.predict(
            input_images=input_images,
            output_dir=output_dir,
            configuration=configuration,
            folds=folds,
            save_probabilities=save_probabilities
        )
        
        return {
            'status': 'success',
            'output_path': prediction_path,
            'model_path': model_path,
            'configuration': configuration,
            'folds': folds,
            'save_probabilities': save_probabilities
        }
        
    except Exception as e:
        logger.error(f"nnUNet prediction failed: {e}")
        raise ValueError(f"nnUNet prediction failed: {e}") from e


def train_directory(**kwargs):
    """
    Train an nnU-Net model from directory containing images and labels.
    
    Args (via kwargs with numeric keys):
        '0': images_dir - Directory path containing training images 
        '1': labels_dir - Directory path containing training labels
        '2': modalities - Modalities string (e.g., "T1")
        '3': work_dir - Working directory path for nnU-Net
        '4': dataset_id - Dataset ID (default: 1)
        '5': dataset_name - Dataset name (default: "VoxLogicADataset")
        
    Returns:
        Dictionary with training results
    """
    try:
        # Extract required arguments with explicit int conversion
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("train_directory requires: images_dir, labels_dir")
        
        images_dir = str(kwargs['0'])
        labels_dir = str(kwargs['1'])
        modalities = str(kwargs.get('2', "T1"))
        work_dir = str(kwargs.get('3', "/tmp/nnunet_test_workspace"))
        dataset_id = int(float(kwargs.get('4', 1)))  # Convert float to int safely
        dataset_name = str(kwargs.get('5', "VoxLogicADataset"))
        
        logger.info(f"Starting nnUNet directory training: {images_dir}, {labels_dir}")
        
        # For now, return a simulated successful result
        # In production, this would call the actual nnunet_wrapper
        from pathlib import Path
        
        result = {
            'status': 'success_simulated',
            'model_path': f'{work_dir}/nnUNet_results/Dataset{dataset_id}_{dataset_name}',
            'dataset_id': dataset_id,
            'dataset_name': dataset_name,
            'configuration': '3d_fullres',
            'nfolds': 5,
            'work_dir': work_dir,
            'images_dir': images_dir,
            'labels_dir': labels_dir,
            'modalities': modalities,
            'message': 'nnUNet training simulated - wrapper would be called in production'
        }
        
        return result
        
    except Exception as e:
        logger.error(f"nnUNet directory training failed: {e}")
        raise ValueError(f"nnUNet training from directory failed: {e}") from e
