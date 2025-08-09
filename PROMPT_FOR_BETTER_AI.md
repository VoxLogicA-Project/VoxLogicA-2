# PROMPT FOR IMPLEMENTING REAL nnU-Net v2 INTEGRATION (Revised)

## Context & Objective
Implement REAL nnU-Net v2 training and inference integration within the VoxLogicA-2 spatial model checker framework. This must create real nnU-Net models and run genuine training/prediction using the official nnUNetv2 CLIs.

## Technical Requirements

### 1. VoxLogicA Integration Architecture
- Primitives live in `implementation/python/voxlogica/primitives/nnunet/__init__.py`
- Functions are invoked with numeric-key kwargs ('0', '1', ...); return dicts
- Use ValueError for user-facing errors with clear messages
- Use standard logging logger (module-level `logging.getLogger(__name__)`)

### 2. Required Functions to Implement

#### `train_directory(**kwargs)`
Must implement REAL nnU-Net v2 training with these arguments:
- `'0'`: images_dir - Directory with training images (.nii.gz)
- `'1'`: labels_dir - Directory with training labels (.nii.gz) 
- `'2'`: modalities - Modalities string (e.g., "T1", "T2", etc.)
- `'3'`: work_dir - nnU-Net working directory for results
- `'4'`: dataset_id - Integer dataset ID (default: 1)
- `'5'`: dataset_name - String dataset name (default: "VoxLogicADataset")
 - `'6'`: configuration - nnU-Net configuration (default: "3d_fullres")
 - `'7'`: nfolds - number of folds (default: 5)

Must return dictionary with:
```python
{
    'status': 'success',
    'model_path': '/path/to/trained/model',
    'dataset_id': int,
    'configuration': '3d_fullres',
    'fold_results': [...],
    'training_time': seconds,
    'final_metrics': {...}
}
```

#### `predict(**kwargs)`
Must implement REAL nnU-Net v2 prediction with these arguments:
- `'0'`: input_images - Directory or single file with images to predict
- `'1'`: model_path - Path to trained model from train_directory
- `'2'`: output_dir - Directory to save predictions
- `'3'`: configuration - nnU-Net config (optional, default: "3d_fullres")
- `'4'`: folds - Folds to use (optional, default: None for all)
- `'5'`: save_probabilities - Boolean (optional, default: False)

Must return dictionary with:
```python
{
    'status': 'success',
    'output_path': '/path/to/predictions',
    'prediction_files': [list_of_created_files],
    'num_predictions': int,
    'inference_time': seconds
}
```

### 3. nnU-Net v2 Integration Requirements

#### Installation & Dependencies
- Do not add torch to requirements; install PyTorch manually for the platform
- Install nnU-Net v2: `pip install nnunetv2`
- Set env vars: `nnUNet_raw`, `nnUNet_preprocessed`, `nnUNet_results` under the provided work_dir
- CLI tools used: `nnUNetv2_plan_and_preprocess`, `nnUNetv2_train`, `nnUNetv2_predict`

#### Real Training Implementation
- Consume directories: labels as source of case IDs, images named `{case}_{0000}.nii.gz`
- Create dataset at `${work_dir}/nnUNet_raw/DatasetXXX_NAME` with imagesTr/labelsTr
- Generate dataset.json with `channel_names`, minimal `labels`, and `file_ending`
- Run `nnUNetv2_plan_and_preprocess -d <id> -c <nfolds> --verify_dataset_integrity`
- Run `nnUNetv2_train <id> <configuration> <fold>` for each fold; stop on error
- Return model path under `${work_dir}/nnUNet_results/DatasetXXX_NAME`

#### Real Prediction Implementation  
- Parse dataset id from `model_path` folder name `DatasetXXX_Name`
- Ensure `nnUNet_results` points to the model root directory
- Run `nnUNetv2_predict -i <input> -o <out> -d <id> -c <cfg> [-f ...] [--save_probabilities]`
- Return list of created files and count

### 4. Error Handling & Robustness
- Check for nnU-Net installation and provide clear error messages
- Validate input directories and file formats
- Handle GPU/CPU resource management
- Provide progress monitoring for long-running operations
- Clean up temporary files appropriately
- Handle memory limitations gracefully

### 5. Expected Behavior Changes
Required (real): Training will take minutes/hours and create actual model files in `work_dir`; prediction writes real segmentations.

### 6. Testing Workflow
The implementation will be exercised by `/tests/test_nnunet_synthetic/test_real_workflow.imgql` which:
1. Loads synthetic medical images (64x64x32 voxels, 20 training images)
2. Calls `nnunet.train_directory()` with real parameters
3. Calls `nnunet.predict()` on the trained model
4. Should create real files and take realistic time

### 7. Integration Points
- File: `/implementation/python/voxlogica/primitives/nnunet/__init__.py`
- Replace any `NotImplementedError` placeholders with real implementations (done)
- Keep the function signatures and kwargs parameter handling exactly as specified
- Maintain compatibility with VoxLogicA's execution environment

## Success Criteria
1. Training creates actual nnU-Net model files in specified work_dir
2. Training takes realistic time (not milliseconds)
3. Prediction uses real trained models and creates actual segmentation files
4. Both functions return accurate metadata and file paths
5. Integration works seamlessly with VoxLogicA's workflow execution
6. Error handling is robust and informative

## Current Environment
- Python environment with numpy, SimpleITK already installed
- macOS system with potential CUDA/CPU execution
- VoxLogicA-2 framework fully functional except for nnU-Net integration
- Synthetic test data available in `/tests/test_nnunet_synthetic/`

IMPLEMENT THIS PROPERLY - NO SIMULATIONS.
