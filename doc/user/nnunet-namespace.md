# nnUNet Namespace Documentation

## Overview

The `nnunet` namespace provides integration between VoxLogicA-2 and nnU-Net v2, enabling seamless medical image segmentation within VoxLogicA workflows. This namespace offers two main functions: `train` for model training and `predict` for inference.

## Features

- **Resume Support**: Both training and prediction support resuming from interrupted executions
- **Dask Integration**: Works natively with VoxLogicA's Dask-based execution system
- **Multi-modal Support**: Handles multi-modal medical imaging data (e.g., T1, T2, FLAIR)
- **Cross-validation**: Built-in support for k-fold cross-validation during training
- **Flexible Configuration**: Supports all major nnU-Net configurations (2d, 3d_fullres, 3d_lowres)

## Prerequisites

Before using the nnUNet namespace, ensure that:

1. **nnunetv2** is installed: `pip install nnunetv2`
2. **PyTorch** is installed manually for your hardware (CPU/GPU/MPS)
3. **System requirements** are met (Python >= 3.9)

## Functions

### nnunet.train

Trains an nnU-Net model from Dask bags with automatic resume support.

**Signature:**
```voxlogica
nnunet.train(images_bag, labels_bag, modalities, work_dir, [dataset_id], [dataset_name], [configuration], [nfolds])
```

**Arguments:**
- `images_bag` (required): Dask bag containing training images with format `(case_id, modality, numpy_array)`
- `labels_bag` (required): Dask bag containing training labels with format `(case_id, numpy_array)`
- `modalities` (required): List of modality names (e.g., `["T1", "T2", "FLAIR"]`)
- `work_dir` (required): Working directory path where nnU-Net files will be stored
- `dataset_id` (optional): Numeric dataset identifier (default: 1)
- `dataset_name` (optional): Human-readable dataset name (default: "VoxLogicADataset")
- `configuration` (optional): nnU-Net configuration ("2d", "3d_fullres", "3d_lowres", default: "3d_fullres")
- `nfolds` (optional): Number of cross-validation folds (default: 5)

**Returns:**
Dictionary containing:
- `status`: "success" if training completed
- `model_path`: Absolute path to the trained model
- `dataset_id`: Dataset identifier used
- `dataset_name`: Dataset name used
- `configuration`: Configuration used
- `nfolds`: Number of folds used
- `work_dir`: Working directory used
- `training_results`: Detailed training results from nnU-Net
- `trained_folds`: List of successfully trained fold numbers

**Example:**
```voxlogica
let result = nnunet.train(images, labels, ["T1", "T2"], "/tmp/nnunet_work", 1, "MyDataset", "3d_fullres", 5)
let model_path = result["model_path"]
```

### nnunet.predict

Runs prediction using a trained nnU-Net model.

**Signature:**
```voxlogica
nnunet.predict(input_images, model_path, output_dir, [configuration], [folds], [save_probabilities])
```

**Arguments:**
- `input_images` (required): Dask bag with test images or directory path containing images
- `model_path` (required): Path to trained model (returned by `nnunet.train`)
- `output_dir` (required): Directory where predictions will be saved
- `configuration` (optional): nnU-Net configuration to use (default: "3d_fullres")
- `folds` (optional): List of specific folds to use for prediction (default: None = all folds)
- `save_probabilities` (optional): Whether to save probability maps (default: false)

**Returns:**
Dictionary containing:
- `status`: "success" if prediction completed
- `output_path`: Path to directory containing predictions
- `model_path`: Model path used
- `configuration`: Configuration used
- `folds`: Folds used for prediction
- `save_probabilities`: Whether probabilities were saved

**Example:**
```voxlogica
let result = nnunet.predict(test_images, model_path, "/tmp/predictions", "3d_fullres", [0, 1, 2], true)
let output_path = result["output_path"]
```

## Complete Workflow Example

```voxlogica
# Prepare data
let training_images = my_training_data_bag  # format: (case_id, modality, array)
let training_labels = my_training_labels_bag  # format: (case_id, array)
let test_images = my_test_data_bag
let modalities = ["T1", "T2", "FLAIR"]

# Train model
let training_result = nnunet.train(
    training_images, 
    training_labels, 
    modalities, 
    "/tmp/nnunet_workspace",
    1,
    "MyMedicalDataset",
    "3d_fullres", 
    5
)

print "Training status: " training_result["status"]
print "Model saved at: " training_result["model_path"]

# Run prediction
let prediction_result = nnunet.predict(
    test_images,
    training_result["model_path"],
    "/tmp/predictions",
    "3d_fullres",
    [0, 1, 2, 3, 4],  # Use all 5 folds
    true  # Save probability maps
)

print "Predictions saved at: " prediction_result["output_path"]
```

## Resume Functionality

Both functions automatically support resume functionality:

- **Training Resume**: If VoxLogicA is interrupted during training, subsequent calls to `nnunet.train` with the same parameters will resume from the last completed fold
- **Prediction Resume**: Predictions can be safely restarted if interrupted

This is achieved through nnU-Net's built-in checkpoint system and VoxLogicA's content-addressed storage.

## Data Format Requirements

### Training Images Bag
Each element should be a tuple: `(case_id, modality, numpy_array)`
- `case_id`: String or integer identifying the case
- `modality`: String matching one of the modalities list (e.g., "T1", "T2")
- `numpy_array`: 3D numpy array representing the image volume

### Training Labels Bag
Each element should be a tuple: `(case_id, numpy_array)`
- `case_id`: String or integer matching the images
- `numpy_array`: 3D numpy array with integer labels (0=background, 1,2,3...=regions)

### Test Images Bag
Same format as training images bag, but without labels.

## Error Handling

The functions provide detailed error messages for common issues:
- Missing required arguments
- Invalid data formats
- nnU-Net installation problems
- File system permissions
- Hardware compatibility issues

## Performance Considerations

- **Memory**: nnU-Net can be memory-intensive; ensure adequate RAM
- **Storage**: Models and preprocessed data require significant disk space
- **GPU**: Training benefits significantly from GPU acceleration
- **Parallelism**: VoxLogicA automatically handles parallel execution where possible

## Integration with VoxLogicA Features

- **Caching**: All operations benefit from VoxLogicA's content-addressed caching
- **Distributed Execution**: Compatible with VoxLogicA's Dask-based distributed execution
- **Debugging**: Integrates with VoxLogicA's debugging and visualization tools
- **Type System**: Properly integrated with VoxLogicA's type system

## Troubleshooting

### Common Issues

1. **"nnUNet wrapper not available"**: Install nnunetv2 with `pip install nnunetv2`
2. **"torch not found"**: Install PyTorch manually from https://pytorch.org/
3. **"Model path does not exist"**: Ensure training completed successfully
4. **Memory errors**: Reduce batch size or use smaller image sizes
5. **Permission errors**: Check write permissions for work_dir and output_dir

### Debug Mode

Enable debug logging to get detailed information:
```bash
./voxlogica run --debug your_nnunet_program.imgql
```

## Technical Details

The nnUNet namespace is implemented as a thin wrapper around the `nnunet_wrapper.py` module, which provides:

- Dask bag integration
- nnU-Net v2 API abstraction
- Automatic dataset format conversion
- Resume functionality
- Error handling and logging

For advanced use cases, users can extend the wrapper or create custom primitives following the same patterns.
