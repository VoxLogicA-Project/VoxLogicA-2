#!/usr/bin/env python3
"""
Test script for nnUNet namespace in VoxLogicA-2

This script tests the nnUNet namespace functionality without requiring
actual nnU-Net installation, using mock data and checking that the
namespace is properly loaded.
"""

import sys
from pathlib import Path

# Add the VoxLogicA implementation to the path
voxlogica_path = Path(__file__).parent.parent.parent.parent.parent / "implementation" / "python"
sys.path.insert(0, str(voxlogica_path))

def test_nnunet_namespace_loading():
    """Test that the nnUNet namespace can be loaded and lists primitives correctly"""
    try:
        from voxlogica.execution import PrimitivesLoader
        
        loader = PrimitivesLoader()
        
        # Test listing nnUNet namespace
        namespaces = loader.list_namespaces()
        print(f"Available namespaces: {namespaces}")
        
        if 'nnunet' not in namespaces:
            print("❌ nnUNet namespace not found in available namespaces")
            return False
        
        # Test listing nnUNet primitives
        nnunet_primitives = loader.list_primitives('nnunet')
        print(f"nnUNet primitives: {nnunet_primitives}")
        
        expected_primitives = ['train', 'predict']
        for primitive in expected_primitives:
            if primitive not in nnunet_primitives:
                print(f"❌ Expected primitive '{primitive}' not found in nnUNet namespace")
                return False
        
        # Test loading primitives
        train_func = loader.load_primitive('nnunet.train')
        if train_func is None:
            print("❌ Failed to load nnunet.train primitive")
            return False
        
        predict_func = loader.load_primitive('nnunet.predict')
        if predict_func is None:
            print("❌ Failed to load nnunet.predict primitive")
            return False
        
        print("✅ nnUNet namespace loaded successfully")
        print(f"✅ train function: {train_func}")
        print(f"✅ predict function: {predict_func}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing nnUNet namespace: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_primitive_arguments():
    """Test that the primitives have proper argument handling"""
    try:
        from voxlogica.execution import PrimitivesLoader
        
        loader = PrimitivesLoader()
        train_func = loader.load_primitive('nnunet.train')
        predict_func = loader.load_primitive('nnunet.predict')
        
        # Test train function argument validation
        try:
            result = train_func()  # No arguments
            print("❌ train function should require arguments")
            return False
        except ValueError as e:
            if "requires" in str(e):
                print("✅ train function properly validates required arguments")
            else:
                print(f"❌ train function error message unexpected: {e}")
                return False
        
        # Test predict function argument validation
        try:
            result = predict_func()  # No arguments
            print("❌ predict function should require arguments")
            return False
        except ValueError as e:
            if "requires" in str(e):
                print("✅ predict function properly validates required arguments")
            else:
                print(f"❌ predict function error message unexpected: {e}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing primitive arguments: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Testing nnUNet namespace for VoxLogicA-2...")
    print("=" * 50)
    
    success = True
    
    print("\n1. Testing namespace loading...")
    success &= test_nnunet_namespace_loading()
    
    print("\n2. Testing primitive argument validation...")
    success &= test_primitive_arguments()
    
    print("\n" + "=" * 50)
    if success:
        print("✅ All tests passed! nnUNet namespace is ready.")
    else:
        print("❌ Some tests failed. Check the output above.")
        sys.exit(1)
