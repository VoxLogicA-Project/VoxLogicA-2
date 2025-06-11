"""
Primitive to save a SimpleITK image to a file
"""
import SimpleITK as sitk

def execute(**kwargs):
    # Expects: {'0': image, '1': filename}
    img = kwargs.get('0')
    filename = kwargs.get('1')
    if not hasattr(img, 'GetPixelID'):  # crude check for SimpleITK image
        raise ValueError("save_sitk_image: argument 0 must be a SimpleITK image")
    if not isinstance(filename, str):
        raise ValueError("save_sitk_image: argument 1 must be a filename string")
    # Remove quotes if present
    if filename.startswith('"') and filename.endswith('"'):
        filename = filename[1:-1]
    sitk.WriteImage(img, filename)
    return filename
