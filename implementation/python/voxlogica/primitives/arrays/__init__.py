"""
Arrays namespace for VoxLogicA-2 primitives

This namespace provides array operations useful for machine learning evaluation,
particularly for measuring accuracy, computing confusion matrices, and other
statistical metrics on image data.
"""

import numpy as np
import SimpleITK as sitk
from typing import Dict, Tuple, Union, List
import logging

logger = logging.getLogger(__name__)


def vector_uint32(values: List[int]) -> List[int]:
    """
    Create a VectorUInt32 for SimpleITK functions.
    
    Args:
        values: List of unsigned integers
        
    Returns:
        List of integers (compatible with SimpleITK VectorUInt32)
    """
    return [int(v) for v in values]


def vector_double(values: List[float]) -> List[float]:
    """
    Create a VectorDouble for SimpleITK functions.
    
    Args:
        values: List of double precision floats
        
    Returns:
        List of floats (compatible with SimpleITK VectorDouble)
    """
    return [float(v) for v in values]


def register_primitives():
    """Register array operation primitives dynamically"""
    primitives = {
        'pixel_accuracy': pixel_accuracy,
        'confusion_matrix': confusion_matrix,
        'dice_score': dice_score,
        'jaccard_index': jaccard_index,
        'vector_uint32': vector_uint32,
        'vector_double': vector_double,
        'count_pixels': count_pixels,
        'threshold_equal': threshold_equal,
        'array_stats': array_stats,
        'compare_arrays': compare_arrays
    }
    return primitives

def list_primitives():
    """List all primitives available in this namespace"""
    return {
        'pixel_accuracy': 'Calculate pixel-wise accuracy between predicted and ground truth images',
        'confusion_matrix': 'Compute confusion matrix between predicted and ground truth images',
        'dice_score': 'Calculate Dice similarity coefficient',
        'jaccard_index': 'Calculate Jaccard index (IoU)',
        'count_pixels': 'Count pixels with specific values in an image',
        'threshold_equal': 'Create binary mask where values equal threshold',
        'array_stats': 'Compute basic statistics of an image array',
        'compare_arrays': 'Compare two arrays element-wise'
    }

def _image_to_array(image):
    """Convert SimpleITK image to numpy array if needed"""
    if hasattr(image, 'GetArrayFromImage'):
        # If it's already a SimpleITK image
        return sitk.GetArrayFromImage(image)
    elif hasattr(image, 'GetSize'):
        # If it's a SimpleITK image
        return sitk.GetArrayFromImage(image)
    else:
        # Assume it's already a numpy array
        return np.array(image)

def pixel_accuracy(**kwargs):
    """
    Calculate pixel-wise accuracy between predicted and ground truth images.
    
    Args (via kwargs with numeric keys):
        '0': predicted - Predicted image/array
        '1': ground_truth - Ground truth image/array
        
    Returns:
        Dictionary with accuracy metrics
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("pixel_accuracy requires: predicted, ground_truth")
            
        predicted = _image_to_array(kwargs['0'])
        ground_truth = _image_to_array(kwargs['1'])
        
        if predicted.shape != ground_truth.shape:
            raise ValueError(f"Shape mismatch: predicted {predicted.shape} vs ground_truth {ground_truth.shape}")
        
        # Calculate pixel-wise accuracy
        correct_pixels = np.sum(predicted == ground_truth)
        total_pixels = np.prod(predicted.shape)
        accuracy = correct_pixels / total_pixels
        
        return {
            'accuracy': float(accuracy),
            'correct_pixels': int(correct_pixels),
            'total_pixels': int(total_pixels),
            'incorrect_pixels': int(total_pixels - correct_pixels)
        }
        
    except Exception as e:
        logger.error(f"pixel_accuracy failed: {e}")
        raise ValueError(f"pixel_accuracy failed: {e}") from e

def confusion_matrix(**kwargs):
    """
    Compute confusion matrix between predicted and ground truth images.
    
    Args (via kwargs with numeric keys):
        '0': predicted - Predicted image/array
        '1': ground_truth - Ground truth image/array
        '2': num_classes - Number of classes (optional, auto-detected if not provided)
        
    Returns:
        Dictionary with confusion matrix and derived metrics
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("confusion_matrix requires: predicted, ground_truth")
            
        predicted = _image_to_array(kwargs['0']).flatten()
        ground_truth = _image_to_array(kwargs['1']).flatten()
        
        if predicted.shape != ground_truth.shape:
            raise ValueError("Arrays must have the same shape")
        
        # Determine number of classes
        if '2' in kwargs:
            num_classes = int(kwargs['2'])
        else:
            num_classes = max(np.max(predicted), np.max(ground_truth)) + 1
        
        # Compute confusion matrix
        cm = np.zeros((num_classes, num_classes), dtype=int)
        for i in range(len(predicted)):
            cm[int(ground_truth[i]), int(predicted[i])] += 1
        
        # Calculate per-class metrics
        precision = np.zeros(num_classes)
        recall = np.zeros(num_classes)
        f1_score = np.zeros(num_classes)
        
        for c in range(num_classes):
            tp = cm[c, c]
            fp = np.sum(cm[:, c]) - tp
            fn = np.sum(cm[c, :]) - tp
            
            precision[c] = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall[c] = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1_score[c] = 2 * precision[c] * recall[c] / (precision[c] + recall[c]) if (precision[c] + recall[c]) > 0 else 0
        
        return {
            'confusion_matrix': cm.tolist(),
            'num_classes': num_classes,
            'precision': precision.tolist(),
            'recall': recall.tolist(),
            'f1_score': f1_score.tolist(),
            'mean_precision': float(np.mean(precision)),
            'mean_recall': float(np.mean(recall)),
            'mean_f1_score': float(np.mean(f1_score))
        }
        
    except Exception as e:
        logger.error(f"confusion_matrix failed: {e}")
        raise ValueError(f"confusion_matrix failed: {e}") from e

def dice_score(**kwargs):
    """
    Calculate Dice similarity coefficient between two binary images.
    
    Args (via kwargs with numeric keys):
        '0': predicted - Predicted binary image/array
        '1': ground_truth - Ground truth binary image/array
        '2': positive_label - Value considered as positive (optional, default: 1)
        
    Returns:
        Dictionary with Dice score
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("dice_score requires: predicted, ground_truth")
            
        predicted = _image_to_array(kwargs['0'])
        ground_truth = _image_to_array(kwargs['1'])
        
        positive_label = int(kwargs.get('2', 1))
        
        # Convert to binary masks
        pred_binary = (predicted == positive_label).astype(int)
        gt_binary = (ground_truth == positive_label).astype(int)
        
        # Calculate Dice score
        intersection = np.sum(pred_binary * gt_binary)
        dice = (2.0 * intersection) / (np.sum(pred_binary) + np.sum(gt_binary))
        
        return {
            'dice_score': float(dice),
            'intersection': int(intersection),
            'predicted_positive': int(np.sum(pred_binary)),
            'ground_truth_positive': int(np.sum(gt_binary))
        }
        
    except Exception as e:
        logger.error(f"dice_score failed: {e}")
        raise ValueError(f"dice_score failed: {e}") from e

def jaccard_index(**kwargs):
    """
    Calculate Jaccard index (Intersection over Union) between two binary images.
    
    Args (via kwargs with numeric keys):
        '0': predicted - Predicted binary image/array
        '1': ground_truth - Ground truth binary image/array
        '2': positive_label - Value considered as positive (optional, default: 1)
        
    Returns:
        Dictionary with Jaccard index
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("jaccard_index requires: predicted, ground_truth")
            
        predicted = _image_to_array(kwargs['0'])
        ground_truth = _image_to_array(kwargs['1'])
        
        positive_label = int(kwargs.get('2', 1))
        
        # Convert to binary masks
        pred_binary = (predicted == positive_label).astype(int)
        gt_binary = (ground_truth == positive_label).astype(int)
        
        # Calculate Jaccard index
        intersection = np.sum(pred_binary * gt_binary)
        union = np.sum(pred_binary) + np.sum(gt_binary) - intersection
        
        jaccard = intersection / union if union > 0 else 0.0
        
        return {
            'jaccard_index': float(jaccard),
            'iou': float(jaccard),  # IoU is the same as Jaccard index
            'intersection': int(intersection),
            'union': int(union)
        }
        
    except Exception as e:
        logger.error(f"jaccard_index failed: {e}")
        raise ValueError(f"jaccard_index failed: {e}") from e

def count_pixels(**kwargs):
    """
    Count pixels with specific values in an image.
    
    Args (via kwargs with numeric keys):
        '0': image - Input image/array
        '1': value - Value to count (optional, if not provided returns count of all unique values)
        
    Returns:
        Dictionary with pixel counts
    """
    try:
        if '0' not in kwargs:
            raise ValueError("count_pixels requires: image")
            
        image = _image_to_array(kwargs['0'])
        
        if '1' in kwargs:
            # Count specific value
            value = kwargs['1']
            count = np.sum(image == value)
            return {
                'value': float(value),
                'count': int(count),
                'total_pixels': int(np.prod(image.shape)),
                'percentage': float(count / np.prod(image.shape) * 100)
            }
        else:
            # Count all unique values
            unique_values, counts = np.unique(image, return_counts=True)
            total_pixels = np.prod(image.shape)
            
            result = {
                'unique_values': unique_values.tolist(),
                'counts': counts.tolist(),
                'total_pixels': int(total_pixels),
                'percentages': (counts / total_pixels * 100).tolist()
            }
            
            return result
        
    except Exception as e:
        logger.error(f"count_pixels failed: {e}")
        raise ValueError(f"count_pixels failed: {e}") from e

def threshold_equal(**kwargs):
    """
    Create binary mask where values equal threshold.
    
    Args (via kwargs with numeric keys):
        '0': image - Input image/array
        '1': threshold - Threshold value
        '2': true_value - Value for pixels equal to threshold (optional, default: 1)
        '3': false_value - Value for pixels not equal to threshold (optional, default: 0)
        
    Returns:
        Binary mask as SimpleITK image
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("threshold_equal requires: image, threshold")
            
        image = kwargs['0']
        threshold = kwargs['1']
        true_value = kwargs.get('2', 1)
        false_value = kwargs.get('3', 0)
        
        # Use SimpleITK Equal function if input is SimpleITK image
        if hasattr(image, 'GetSize'):
            # Create threshold image
            threshold_image = sitk.Image(image.GetSize(), image.GetPixelID())
            threshold_image.CopyInformation(image)
            threshold_array = np.full(sitk.GetArrayFromImage(image).shape, threshold)
            threshold_image = sitk.GetImageFromArray(threshold_array)
            threshold_image.CopyInformation(image)
            
            # Use SimpleITK Equal
            result = sitk.Equal(image, threshold_image, false_value, true_value)
            return result
        else:
            # Work with numpy arrays
            image_array = np.array(image)
            result_array = np.where(image_array == threshold, true_value, false_value)
            
            # Convert back to SimpleITK image
            result_image = sitk.GetImageFromArray(result_array)
            return result_image
            
    except Exception as e:
        logger.error(f"threshold_equal failed: {e}")
        raise ValueError(f"threshold_equal failed: {e}") from e

def array_stats(**kwargs):
    """
    Compute basic statistics of an image array.
    
    Args (via kwargs with numeric keys):
        '0': image - Input image/array
        
    Returns:
        Dictionary with statistics
    """
    try:
        if '0' not in kwargs:
            raise ValueError("array_stats requires: image")
            
        image = _image_to_array(kwargs['0'])
        
        return {
            'mean': float(np.mean(image)),
            'std': float(np.std(image)),
            'min': float(np.min(image)),
            'max': float(np.max(image)),
            'median': float(np.median(image)),
            'shape': list(image.shape),
            'total_elements': int(np.prod(image.shape)),
            'unique_values': int(len(np.unique(image)))
        }
        
    except Exception as e:
        logger.error(f"array_stats failed: {e}")
        raise ValueError(f"array_stats failed: {e}") from e

def compare_arrays(**kwargs):
    """
    Compare two arrays element-wise and provide detailed comparison.
    
    Args (via kwargs with numeric keys):
        '0': array1 - First array/image
        '1': array2 - Second array/image
        
    Returns:
        Dictionary with comparison results
    """
    try:
        if '0' not in kwargs or '1' not in kwargs:
            raise ValueError("compare_arrays requires: array1, array2")
            
        array1 = _image_to_array(kwargs['0'])
        array2 = _image_to_array(kwargs['1'])
        
        if array1.shape != array2.shape:
            raise ValueError(f"Shape mismatch: {array1.shape} vs {array2.shape}")
        
        # Element-wise comparison
        equal = np.equal(array1, array2)
        different = np.not_equal(array1, array2)
        
        # Numerical differences (for numerical arrays)
        try:
            diff = array2 - array1
            abs_diff = np.abs(diff)
            
            return {
                'shapes_match': True,
                'elements_equal': int(np.sum(equal)),
                'elements_different': int(np.sum(different)),
                'total_elements': int(np.prod(array1.shape)),
                'percent_equal': float(np.sum(equal) / np.prod(array1.shape) * 100),
                'mean_difference': float(np.mean(diff)),
                'mean_absolute_difference': float(np.mean(abs_diff)),
                'max_absolute_difference': float(np.max(abs_diff)),
                'min_difference': float(np.min(diff)),
                'max_difference': float(np.max(diff))
            }
        except:
            # If numerical operations fail, return basic comparison
            return {
                'shapes_match': True,
                'elements_equal': int(np.sum(equal)),
                'elements_different': int(np.sum(different)),
                'total_elements': int(np.prod(array1.shape)),
                'percent_equal': float(np.sum(equal) / np.prod(array1.shape) * 100)
            }
        
    except Exception as e:
        logger.error(f"compare_arrays failed: {e}")
        raise ValueError(f"compare_arrays failed: {e}") from e
