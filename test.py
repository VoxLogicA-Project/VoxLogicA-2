#!/usr/bin/env python3
"""
Python script that performs the same operations as test_sitk.imgql
using direct SimpleITK calls.

This script demonstrates:
1. Loading a medical image (NIfTI format)
2. Applying binary thresholding
3. Saving the result in different formats
"""

import SimpleITK as sitk
import sys
from pathlib import Path

def main():
    """Main function that replicates test_sitk.imgql behavior"""
    
    print("=== SimpleITK Direct Python Test ===", flush=True)
    
    # Check if input file exists
    input_file = "tests/chris_t1.nii.gz"
    print(f"Checking for input file: {input_file}", flush=True)
    if not Path(input_file).exists():
        print(f"Error: Input file {input_file} not found", flush=True)
        print("Please ensure the test data file exists", flush=True)
        sys.exit(1)
    else:
        print(f"✓ Input file found: {input_file}", flush=True)
    
    try:
        # Step 1: Load the image (equivalent to: let img = ReadImage("tests/chris_t1.nii.gz"))
        print(f"Loading image from {input_file}...", flush=True)
        img = sitk.ReadImage(input_file)
        print(f"  Image size: {img.GetSize()}", flush=True)
        print(f"  Image spacing: {img.GetSpacing()}", flush=True)
        print(f"  Image origin: {img.GetOrigin()}", flush=True)
        print(f"  Pixel type: {img.GetPixelIDTypeAsString()}", flush=True)
        
        # Step 2: Set threshold values (equivalent to VoxLogicA: BinaryThreshold(img, 0, 100, 255, 0))
        lower_threshold = 0
        upper_threshold = 100  
        inside_value = 255
        outside_value = 0
        print(f"Using threshold values: lower={lower_threshold}, upper={upper_threshold}, inside={inside_value}, outside={outside_value}", flush=True)
        
        # Step 3: Apply binary threshold (equivalent to: let thresholded = BinaryThreshold(img, 0, 100, 255, 0))
        print("Applying binary threshold...", flush=True)
        # BinaryThreshold(image, lowerThreshold, upperThreshold, insideValue, outsideValue=0)
        # The imgql version uses: BinaryThreshold(img, 0, 100, 255, 0)
        # This maps to: BinaryThreshold(img, lowerThreshold=0, upperThreshold=100, insideValue=255)
        thresholded = sitk.BinaryThreshold(img, 
                                         lowerThreshold=lower_threshold, 
                                         upperThreshold=upper_threshold, 
                                         insideValue=inside_value,
                                         outsideValue=outside_value)
        
        print(f"  Thresholded image size: {thresholded.GetSize()}")
        print(f"  Thresholded image pixel type: {thresholded.GetPixelIDTypeAsString()}")
        
        # Step 4: Save the result (equivalent to: save "output.png" thresholded)
        output_file = "output.png"
        print(f"Saving result to {output_file}...")
        
        # For PNG output, we need to convert 3D to 2D (extract middle slice)
        if thresholded.GetDimension() == 3:
            size = thresholded.GetSize()
            middle_slice = size[2] // 2
            print(f"  Extracting middle slice {middle_slice} from 3D volume")
            slice_image = thresholded[:, :, middle_slice]
            sitk.WriteImage(slice_image, output_file)
        else:
            sitk.WriteImage(thresholded, output_file)
        
        print(f"  Successfully saved to {output_file}")
        
        # Additional saves to demonstrate different formats (like the custom serializer system)
        print("\nSaving to additional formats:")
        
        # Save as NIfTI compressed (medical format)
        nii_output = "output_medical.nii.gz"
        sitk.WriteImage(thresholded, nii_output)
        print(f"  Saved medical format: {nii_output}")
        
        # Save as uncompressed NIfTI
        nii_uncompressed = "output_uncompressed.nii"
        sitk.WriteImage(thresholded, nii_uncompressed)
        print(f"  Saved uncompressed format: {nii_uncompressed}")
        
        # Print some statistics about the thresholded image
        print("\n=== Image Statistics ===")
        stats_filter = sitk.StatisticsImageFilter()
        stats_filter.Execute(thresholded)
        print(f"  Min value: {stats_filter.GetMinimum()}")
        print(f"  Max value: {stats_filter.GetMaximum()}")
        print(f"  Mean value: {stats_filter.GetMean():.2f}")
        print(f"  Standard deviation: {stats_filter.GetSigma():.2f}")
        
        print("\n=== Test Completed Successfully ===")
        
    except Exception as e:
        print(f"Error during processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
