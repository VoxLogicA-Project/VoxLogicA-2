"""
Primitive to threshold a SimpleITK image
"""
import SimpleITK as sitk

def execute(**kwargs):
    # Expects: {'0': image, '1': threshold_value}
    img = kwargs.get('0')
    threshold_value = kwargs.get('1')
    if not hasattr(img, 'GetPixelID'):
        raise ValueError("threshold: argument 0 must be a SimpleITK image")
    if not isinstance(threshold_value, (int, float)):
        raise ValueError("threshold: argument 1 must be a number")
    # Set upper threshold to a very large value to avoid lower > upper error
    upper = threshold_value + 1e6
    result = sitk.BinaryThreshold(img, lowerThreshold=threshold_value, upperThreshold=upper, insideValue=1, outsideValue=0)
    return result
