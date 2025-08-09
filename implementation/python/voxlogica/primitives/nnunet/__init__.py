"""
nnUNet namespace for VoxLogicA-2 primitives

This namespace provides nnU-Net v2 training and prediction functionality
integrated with VoxLogicA's execution system. Supports resume functionality
and distributed execution through Dask.

Functions:
- train: Train an nnU-Net model from Dask bags
- predict: Run prediction using a trained nnU-Net model (directory or bag)
- train_directory: Train an nn-U-Net model from directories with images/labels
"""

import os
import sys
import json
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Dict, Callable, Any, Optional, List, Union
import dask.bag as db
from datetime import datetime
import re
import subprocess
import importlib.util

logger = logging.getLogger(__name__)

def get_primitives():
    """
    Return a dictionary of all primitive functions in this namespace.
    
    Each function should be accessible as nnunet.function_name() in VoxLogicA.
    """
    primitives = {
        'train': train,
        'predict': predict,
    'train_directory': train_directory,  # Renamed to avoid conflicts
    'env_check': env_check,
    }
    return primitives

def list_primitives():
    """List all primitives available in this namespace"""
    return {
        'train': 'Train an nnU-Net model from Dask bags with resume support',
        'predict': 'Run prediction using a trained nnU-Net model',
    'train_directory': 'Train an nn-U-Net model from directories with images and labels',
    'env_check': 'Report availability/versions of torch and nnunetv2'
    }

def register_primitives():
    """Register all primitives for dynamic loading"""
    return get_primitives()

def env_check(**kwargs):
    """Report environment readiness for nnUNet (torch and nnunetv2)."""
    torch_ok = False
    torch_version = None
    nnunet_ok = False
    nnunet_version = None
    reasons = []
    try:
        import importlib
        try:
            import torch  # type: ignore
            torch_ok = True
            torch_version = getattr(torch, '__version__', 'unknown')
        except Exception as e:  # noqa: BLE001
            reasons.append(f"torch missing: {e}")
        try:
            nnm = importlib.import_module('nnunetv2')
            nnunet_ok = True
            nnunet_version = getattr(nnm, '__version__', 'unknown')
        except Exception as e:  # noqa: BLE001
            reasons.append(f"nnunetv2 missing: {e}")
    except Exception as e:  # noqa: BLE001
        reasons.append(f"unexpected error: {e}")
    return {
        'torch_available': torch_ok,
        'torch_version': torch_version,
        'nnunetv2_available': nnunet_ok,
        'nnunetv2_version': nnunet_version,
        'ready': torch_ok and nnunet_ok,
        'issues': reasons,
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    }

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
            wrapper_path = nnunet_wrapper_path / "nnunet_wrapper.py"
            if not wrapper_path.exists():
                raise ImportError(f"nnunet_wrapper.py not found at {wrapper_path}")
            spec = importlib.util.spec_from_file_location("nnunet_wrapper", str(wrapper_path))
            if spec is None or spec.loader is None:
                raise ImportError("Could not create spec for nnunet_wrapper")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            nnUNetDaskWrapper = getattr(module, 'nnUNetDaskWrapper')
        except Exception as e:
            raise ValueError(f"nnUNet wrapper not available: {e}. Please ensure nnunetv2 is installed (pip install nnunetv2) and PyTorch is installed for your platform.")
        
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
    Run prediction using a trained nn-U-Net model.
    
    Args (via kwargs with numeric keys):
        '0': input_images - Directory or file path containing images to predict on
        '1': model_path - Path to trained model root (e.g., <work_dir>/nnUNet_results/DatasetXXX_Name)
        '2': output_dir - Directory to save predictions
        '3': configuration - nn-U-Net configuration (optional, default: "3d_fullres")
        '4': folds - List of folds to use (optional, default: None = all folds)
        '5': save_probabilities - Save probability maps (optional, default: False)
        
    Returns:
        Dictionary with prediction results and output path
    """
    try:
        # Required
        if '0' not in kwargs or '1' not in kwargs or '2' not in kwargs:
            raise ValueError("predict requires: input_images, model_path, output_dir")

        input_images = Path(str(kwargs['0']))
        model_path = Path(str(kwargs['1']))
        output_dir = Path(str(kwargs['2']))
        configuration = str(kwargs.get('3', '3d_fullres'))
        folds = kwargs.get('4', None)
        save_probabilities = bool(kwargs.get('5', False))

        # nnUNet availability check
        if importlib.util.find_spec("nnunetv2") is None:
            raise ValueError("nnunetv2 not installed. Please install with 'pip install nnunetv2' and ensure PyTorch is installed for your platform.")

        if not model_path.exists():
            raise ValueError(f"Model path does not exist: {model_path}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Parse dataset_id from model_path name (expects DatasetXXX_Name)
        m = re.search(r"Dataset(\d{3})_", model_path.name)
        if not m:
            raise ValueError(f"Could not parse dataset id from model_path folder name: {model_path.name}")
        dataset_id = int(m.group(1))

        # Configure nnUNet env to use this model path
        # model_path should be <...>/nnUNet_results/DatasetXXX_Name
        nnunet_results_root = model_path.parent
        os.environ['nnUNet_results'] = str(nnunet_results_root)

        # Build command
        cmd = [
            "nnUNetv2_predict",
            "-i", str(input_images),
            "-o", str(output_dir),
            "-d", str(dataset_id),
            "-c", configuration
        ]
        if folds is not None:
            # nnUNet expects -f <fold1> <fold2> ...
            if isinstance(folds, list):
                cmd.extend(["-f"] + [str(int(f)) for f in folds])
            else:
                cmd.extend(["-f", str(int(folds))])
        if save_probabilities:
            cmd.append("--save_probabilities")

        logger.info(f"Running: {' '.join(cmd)} (cwd={model_path.parent.parent if model_path.parent else os.getcwd()})")
        pr = subprocess.run(cmd, capture_output=True, text=True)
        if pr.returncode != 0:
            logger.error(pr.stderr)
            raise ValueError(f"nnUNet predict failed: {pr.stderr}")

        # Collect outputs
        created = sorted([str(p) for p in output_dir.glob("*.nii*")])
        return {
            'status': 'success',
            'output_path': str(output_dir),
            'prediction_files': created,
            'num_predictions': len(created)
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
        '2': modalities - Modalities string or list (e.g., "T1")
        '3': work_dir - Working directory path for nnU-Net
        '4': dataset_id - Dataset ID (default: 1)
        '5': dataset_name - Dataset name (default: "VoxLogicADataset")
        '6': configuration - nnU-Net configuration (default: "2d")
        '7': nfolds - number of folds (default: 1)
        
    Returns:
        Dictionary with training results
    """
    try:
        # Required args
        if '0' not in kwargs or '1' not in kwargs or '2' not in kwargs or '3' not in kwargs:
            raise ValueError("train_directory requires: images_dir, labels_dir, modalities, work_dir [dataset_id, dataset_name]")

        images_dir = Path(str(kwargs['0']))
        labels_dir = Path(str(kwargs['1']))
        modalities_arg = kwargs['2']  # may be string (e.g., "T1") or list
        work_dir = Path(str(kwargs['3']))
        dataset_id = int(kwargs.get('4', 1))
        dataset_name = str(kwargs.get('5', "VoxLogicADataset"))
        configuration = str(kwargs.get('6', "2d"))
        nfolds = int(kwargs.get('7', 1))

        if not images_dir.exists() or not images_dir.is_dir():
            raise ValueError(f"images_dir does not exist or is not a directory: {images_dir}")
        if not labels_dir.exists() or not labels_dir.is_dir():
            raise ValueError(f"labels_dir does not exist or is not a directory: {labels_dir}")

        # Validate nnUNet CLI availability
        if importlib.util.find_spec("nnunetv2") is None:
            raise ValueError("nnunetv2 not installed. Please install with 'pip install nnunetv2' and ensure PyTorch is installed for your platform.")

        # Prepare nnU-Net workspace
        nnunet_raw = work_dir / "nnUNet_raw"
        nnunet_preprocessed = work_dir / "nnUNet_preprocessed"
        nnunet_results = work_dir / "nnUNet_results"
        for d in (nnunet_raw, nnunet_preprocessed, nnunet_results):
            d.mkdir(parents=True, exist_ok=True)

        os.environ['nnUNet_raw'] = str(nnunet_raw)
        os.environ['nnUNet_preprocessed'] = str(nnunet_preprocessed)
        os.environ['nnUNet_results'] = str(nnunet_results)

        dataset_full_name = f"Dataset{dataset_id:03d}_{dataset_name}"
        dataset_dir = nnunet_raw / dataset_full_name
        imagesTr = dataset_dir / 'imagesTr'
        labelsTr = dataset_dir / 'labelsTr'
        imagesTs = dataset_dir / 'imagesTs'
        for d in (imagesTr, labelsTr, imagesTs):
            d.mkdir(parents=True, exist_ok=True)

        # Discover cases from labels (authoritative)
        label_files = sorted([p for p in labels_dir.glob("*.nii*") if p.is_file()])
        if not label_files:
            raise ValueError(f"No label files found in {labels_dir}")

        # Build mapping case_id -> list of modality files in images_dir
        # Expect filenames like case_000_0000.nii.gz and labels case_000.nii.gz
        cases = []
        for lbl in label_files:
            base = lbl.name.split('.')[0]
            case_id = base  # without extension
            cases.append((case_id, lbl))

        # Normalize modalities input to list
        if isinstance(modalities_arg, str):
            modalities: List[str] = [modalities_arg]
        elif isinstance(modalities_arg, list):
            modalities = [str(m) for m in modalities_arg]
        else:
            raise ValueError("modalities must be a string or list of strings")

        # Link/copy files into nnUNet_raw structure
        def _symlink_or_copy(src: Path, dst: Path):
            try:
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                os.symlink(src, dst)
            except Exception:
                # Fallback to copy
                shutil.copy2(src, dst)

        for case_name, lbl_path in cases:
            # create channel files for each modality index
            for mod_idx, _mod in enumerate(modalities):
                # Try to find matching image file: {case_name}_{mod_idx:04d}.*
                pattern = f"{case_name}_{mod_idx:04d}*"
                matches = list(images_dir.glob(pattern))
                if not matches:
                    # If no file for this modality, skip with warning
                    logger.warning(f"Missing modality index {mod_idx:04d} for case {case_name} in {images_dir}")
                    continue
                img_src = matches[0]
                img_dst = imagesTr / f"{case_name}_{mod_idx:04d}{img_src.suffix if img_src.suffix != '' else '.nii.gz'}"
                _symlink_or_copy(img_src, img_dst)

            # Link/copy label
            lbl_dst = labelsTr / f"{case_name}{''.join(lbl_path.suffixes)}"
            _symlink_or_copy(lbl_path, lbl_dst)

        # Create dataset.json
        channel_names = {str(i): mod for i, mod in enumerate(modalities)}
        dataset_json = {
            "channel_names": channel_names,
            "labels": {
                "background": 0,
                "label_1": 1
            },
            "numTraining": len(cases),
            "file_ending": ".nii.gz",
            "dataset_name": dataset_name
        }
        (dataset_dir / 'dataset.json').write_text(json.dumps(dataset_json, indent=2))

        # Run planning & preprocessing
        # Preprocess the dataset for the selected configuration only (faster)
        plan_cmd = [
            "nnUNetv2_plan_and_preprocess",
            "-d", str(dataset_id),
            "--verify_dataset_integrity",
            "-c", configuration
        ]
        logger.info(f"Running: {' '.join(plan_cmd)} (cwd={work_dir})")
        pp = subprocess.run(plan_cmd, cwd=str(work_dir), capture_output=True, text=True)
        if pp.returncode != 0:
            logger.error(pp.stderr)
            raise ValueError(f"nnUNet preprocessing failed: {pp.stderr}")

        # Train folds
        fold_results: List[Dict[str, Any]] = []
        start = datetime.now()
        for fold in range(nfolds):
            train_cmd = [
                "nnUNetv2_train",
                str(dataset_id),
                configuration,
                str(fold)
            ]
            logger.info(f"Running: {' '.join(train_cmd)} (cwd={work_dir})")
            tr = subprocess.run(train_cmd, cwd=str(work_dir), capture_output=True, text=True)
            status = 'success' if tr.returncode == 0 else 'failed'
            fold_results.append({
                'fold': fold,
                'status': status,
                'stdout': tr.stdout[-2000:],
                'stderr': tr.stderr[-2000:]
            })
            if tr.returncode != 0:
                # Stop on first failure
                raise ValueError(f"nnUNet training fold {fold} failed: {tr.stderr}")

        training_time = (datetime.now() - start).total_seconds()
        model_path = nnunet_results / dataset_full_name

        return {
            'status': 'success',
            'model_path': str(model_path),
            'dataset_id': dataset_id,
            'configuration': configuration,
            'fold_results': fold_results,
            'training_time': training_time,
            'final_metrics': {}
        }
    except Exception as e:
        logger.error(f"nnUNet train_directory failed: {e}")
        raise ValueError(f"nnUNet train_directory failed: {e}") from e


# (Removed duplicate old predict function)
