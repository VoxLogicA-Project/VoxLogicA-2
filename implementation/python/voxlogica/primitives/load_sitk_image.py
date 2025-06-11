"""
Primitive to load a SimpleITK image from a file (e.g., NIfTI)
"""
import SimpleITK as sitk

def execute(**kwargs):
    # Expects: {'0': filename}
    filename = kwargs.get('0')
    if not isinstance(filename, str):
        raise ValueError("load_sitk_image: argument 0 must be a filename string")
    # Remove quotes if present
    if filename.startswith('"') and filename.endswith('"'):
        filename = filename[1:-1]
    img = sitk.ReadImage(filename)
    return img
