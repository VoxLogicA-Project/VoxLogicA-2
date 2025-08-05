# Implementing New Operators and Primitives

## Overview

VoxLogicA-2 uses a dynamic, plugin-based system for implementing new operators and primitives. This system allows domain experts to easily extend the language with specialized operations without modifying the core language implementation.

## Architecture

### Primitive System Components

1. **Primitive Modules**: Python modules implementing specific operations
2. **Registration System**: Automatic discovery and registration of primitives
3. **Execution Integration**: Seamless integration with the execution engine
4. **Type System**: Type checking and validation for primitive operations

### Directory Structure

```
implementation/python/voxlogica/primitives/
├── __init__.py                    # Registration and discovery
├── default/                       # Built-in primitive operations
│   ├── __init__.py
│   ├── arithmetic.py              # Basic math operations
│   ├── collections.py             # List/array operations
│   ├── strings.py                 # String manipulation
│   └── logic.py                   # Boolean operations
├── simpleitk/                     # Medical imaging primitives
│   ├── __init__.py
│   ├── io.py                      # Image loading/saving
│   ├── filters.py                 # Image filtering operations
│   ├── segmentation.py            # Segmentation algorithms
│   └── statistics.py              # Image statistics
└── test/                          # Testing and validation primitives
    ├── __init__.py
    ├── mock_operations.py         # Mock operations for testing
    └── validation.py              # Validation utilities
```

## Creating New Primitives

### Step 1: Define Primitive Class

Create a new primitive by inheriting from `PrimitiveOperation`:

```python
# primitives/custom/my_operation.py

from voxlogica.primitives.base import PrimitiveOperation
from typing import List, Any
import numpy as np

class CustomFilterOperation(PrimitiveOperation):
    """Custom image filtering operation."""
    
    def __init__(self):
        super().__init__(
            name="custom_filter",
            description="Apply custom filtering algorithm to image data"
        )
        # Define input and output types
        self.input_types = [np.ndarray, float, str]
        self.output_type = np.ndarray
        self.category = "image_processing"
        self.version = "1.0"
    
    def execute(self, image: np.ndarray, threshold: float, mode: str) -> np.ndarray:
        """
        Execute the custom filtering operation.
        
        Args:
            image: Input image as numpy array
            threshold: Filtering threshold value
            mode: Filtering mode ("gaussian", "median", "bilateral")
            
        Returns:
            Filtered image as numpy array
            
        Raises:
            ValueError: If mode is not supported
        """
        if mode == "gaussian":
            return self._apply_gaussian_filter(image, threshold)
        elif mode == "median":
            return self._apply_median_filter(image, threshold)
        elif mode == "bilateral":
            return self._apply_bilateral_filter(image, threshold)
        else:
            raise ValueError(f"Unsupported filtering mode: {mode}")
    
    def _apply_gaussian_filter(self, image: np.ndarray, sigma: float) -> np.ndarray:
        """Apply Gaussian filter implementation."""
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(image, sigma=sigma)
    
    def _apply_median_filter(self, image: np.ndarray, size: float) -> np.ndarray:
        """Apply median filter implementation."""
        from scipy.ndimage import median_filter
        return median_filter(image, size=int(size))
    
    def _apply_bilateral_filter(self, image: np.ndarray, sigma: float) -> np.ndarray:
        """Apply bilateral filter implementation."""
        # Custom bilateral filter implementation
        # ... implementation details ...
        return image  # Placeholder
    
    def validate_inputs(self, *args) -> bool:
        """Custom input validation."""
        if not super().validate_inputs(*args):
            return False
        
        image, threshold, mode = args
        
        # Validate image dimensions
        if len(image.shape) not in [2, 3]:
            return False
        
        # Validate threshold range
        if threshold < 0:
            return False
        
        # Validate mode
        if mode not in ["gaussian", "median", "bilateral"]:
            return False
        
        return True
    
    def get_metadata(self) -> dict:
        """Extended metadata for documentation."""
        metadata = super().get_metadata()
        metadata.update({
            'supported_modes': ["gaussian", "median", "bilateral"],
            'input_constraints': {
                'image': "2D or 3D numpy array",
                'threshold': "Non-negative float",
                'mode': "One of: gaussian, median, bilateral"
            },
            'performance_notes': "Bilateral filtering is computationally expensive for large images",
            'examples': [
                {
                    'description': "Apply Gaussian filter",
                    'code': 'custom_filter(image, 2.0, "gaussian")'
                }
            ]
        })
        return metadata
```

### Step 2: Register the Primitive

Primitives are automatically discovered and registered:

```python
# primitives/custom/__init__.py

from .my_operation import CustomFilterOperation

# Registration happens automatically via discovery
# No explicit registration needed
```

### Step 3: Add Tests

Create comprehensive tests for the new primitive:

```python
# tests/test_custom_primitives.py

import pytest
import numpy as np
from voxlogica.primitives import PrimitiveRegistry

class TestCustomFilterOperation:
    
    def setup_method(self):
        """Setup test fixtures."""
        self.primitive = PrimitiveRegistry.get_primitive("custom_filter", "custom")
        self.test_image = np.random.rand(100, 100)
    
    def test_gaussian_filtering(self):
        """Test Gaussian filtering mode."""
        result = self.primitive.execute(self.test_image, 1.0, "gaussian")
        
        assert result.shape == self.test_image.shape
        assert result.dtype == self.test_image.dtype
        # Gaussian filtering should smooth the image
        assert np.var(result) < np.var(self.test_image)
    
    def test_median_filtering(self):
        """Test median filtering mode."""
        # Add some noise to test image
        noisy_image = self.test_image.copy()
        noisy_image[50, 50] = 10.0  # Salt noise
        
        result = self.primitive.execute(noisy_image, 3.0, "median")
        
        assert result.shape == noisy_image.shape
        # Median filter should remove salt noise
        assert result[50, 50] < 1.0
    
    def test_input_validation(self):
        """Test input validation."""
        # Test invalid mode
        with pytest.raises(ValueError, match="Unsupported filtering mode"):
            self.primitive.execute(self.test_image, 1.0, "invalid_mode")
        
        # Test invalid threshold
        assert not self.primitive.validate_inputs(self.test_image, -1.0, "gaussian")
        
        # Test invalid image dimensions
        invalid_image = np.random.rand(10, 10, 10, 10)  # 4D image
        assert not self.primitive.validate_inputs(invalid_image, 1.0, "gaussian")
    
    def test_metadata(self):
        """Test primitive metadata."""
        metadata = self.primitive.get_metadata()
        
        assert metadata['name'] == "custom_filter"
        assert 'supported_modes' in metadata
        assert len(metadata['supported_modes']) == 3
        assert 'examples' in metadata
```

## Advanced Primitive Features

### Parameterized Primitives

```python
class ParameterizedConvolutionOperation(PrimitiveOperation):
    """Convolution operation with configurable kernels."""
    
    def __init__(self, kernel_type: str = "gaussian"):
        super().__init__(
            name=f"conv_{kernel_type}",
            description=f"Convolution with {kernel_type} kernel"
        )
        self.kernel_type = kernel_type
        self.input_types = [np.ndarray, tuple]  # image, kernel_size
        self.output_type = np.ndarray
    
    def execute(self, image: np.ndarray, kernel_size: tuple) -> np.ndarray:
        """Execute convolution with specified kernel."""
        kernel = self._generate_kernel(kernel_size)
        return self._convolve(image, kernel)
    
    def _generate_kernel(self, size: tuple) -> np.ndarray:
        """Generate convolution kernel based on type."""
        if self.kernel_type == "gaussian":
            return self._gaussian_kernel(size)
        elif self.kernel_type == "laplacian":
            return self._laplacian_kernel(size)
        elif self.kernel_type == "sobel":
            return self._sobel_kernel(size)
        else:
            raise ValueError(f"Unknown kernel type: {self.kernel_type}")

# Register multiple variants
gaussian_conv = ParameterizedConvolutionOperation("gaussian")
laplacian_conv = ParameterizedConvolutionOperation("laplacian")
sobel_conv = ParameterizedConvolutionOperation("sobel")
```

### GPU-Accelerated Primitives

```python
class GPUAcceleratedOperation(PrimitiveOperation):
    """GPU-accelerated primitive using CuPy."""
    
    def __init__(self):
        super().__init__(
            name="gpu_matrix_multiply",
            description="GPU-accelerated matrix multiplication"
        )
        self.input_types = [np.ndarray, np.ndarray]
        self.output_type = np.ndarray
        self.requires_gpu = True
    
    def execute(self, matrix_a: np.ndarray, matrix_b: np.ndarray) -> np.ndarray:
        """Execute GPU-accelerated matrix multiplication."""
        try:
            import cupy as cp
            
            # Transfer to GPU
            gpu_a = cp.asarray(matrix_a)
            gpu_b = cp.asarray(matrix_b)
            
            # Perform computation on GPU
            gpu_result = cp.matmul(gpu_a, gpu_b)
            
            # Transfer back to CPU
            result = cp.asnumpy(gpu_result)
            
            return result
            
        except ImportError:
            # Fallback to CPU if CuPy not available
            return np.matmul(matrix_a, matrix_b)
    
    def is_available(self) -> bool:
        """Check if GPU acceleration is available."""
        try:
            import cupy as cp
            return cp.cuda.is_available()
        except ImportError:
            return False
```

### Streaming Primitives

```python
class StreamingOperation(PrimitiveOperation):
    """Primitive that processes data in streaming fashion."""
    
    def __init__(self):
        super().__init__(
            name="streaming_sum",
            description="Compute sum of large arrays using streaming"
        )
        self.input_types = [str]  # File path
        self.output_type = float
        self.supports_streaming = True
    
    def execute(self, file_path: str, chunk_size: int = 1024*1024) -> float:
        """Execute streaming sum operation."""
        total = 0.0
        
        # Process file in chunks
        with open(file_path, 'rb') as f:
            while True:
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                
                # Convert bytes to numbers (simplified)
                chunk_array = np.frombuffer(chunk_data, dtype=np.float32)
                total += np.sum(chunk_array)
        
        return total
    
    def estimate_memory_usage(self, *args) -> int:
        """Estimate memory usage for streaming operation."""
        # Streaming operations use constant memory
        return 1024 * 1024  # 1MB buffer
```

## Integration with Type System

### Type Checking

```python
from typing import Union, TypeVar, Generic

T = TypeVar('T')

class TypedPrimitive(PrimitiveOperation, Generic[T]):
    """Type-safe primitive operation."""
    
    def __init__(self, name: str, description: str, input_type: type, output_type: type):
        super().__init__(name, description)
        self.input_type = input_type
        self.output_type = output_type
    
    def execute(self, data: T) -> T:
        """Type-safe execution."""
        if not isinstance(data, self.input_type):
            raise TypeError(f"Expected {self.input_type}, got {type(data)}")
        
        result = self._execute_typed(data)
        
        if not isinstance(result, self.output_type):
            raise TypeError(f"Output type mismatch: expected {self.output_type}, got {type(result)}")
        
        return result
    
    def _execute_typed(self, data: T) -> T:
        """Override this method in subclasses."""
        raise NotImplementedError()

class StringUpperOperation(TypedPrimitive[str]):
    """Type-safe string uppercase operation."""
    
    def __init__(self):
        super().__init__("str_upper", "Convert string to uppercase", str, str)
    
    def _execute_typed(self, data: str) -> str:
        return data.upper()
```

### Schema Validation

```python
from jsonschema import validate, ValidationError

class SchemaValidatedOperation(PrimitiveOperation):
    """Primitive with JSON schema validation."""
    
    def __init__(self):
        super().__init__(
            name="process_metadata",
            description="Process image metadata with validation"
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1},
                "channels": {"type": "integer", "minimum": 1, "maximum": 4},
                "format": {"type": "string", "enum": ["RGB", "RGBA", "GRAYSCALE"]}
            },
            "required": ["width", "height", "channels", "format"]
        }
    
    def execute(self, metadata: dict) -> dict:
        """Execute with schema validation."""
        try:
            validate(instance=metadata, schema=self.input_schema)
        except ValidationError as e:
            raise ValueError(f"Invalid metadata schema: {e.message}")
        
        # Process validated metadata
        return self._process_metadata(metadata)
    
    def _process_metadata(self, metadata: dict) -> dict:
        """Process validated metadata."""
        return {
            **metadata,
            'total_pixels': metadata['width'] * metadata['height'],
            'aspect_ratio': metadata['width'] / metadata['height']
        }
```

## Performance Optimization

### Compiled Primitives

```python
import numba

class CompiledOperation(PrimitiveOperation):
    """Primitive using Numba JIT compilation."""
    
    def __init__(self):
        super().__init__(
            name="fast_convolution",
            description="JIT-compiled convolution operation"
        )
        self.input_types = [np.ndarray, np.ndarray]
        self.output_type = np.ndarray
    
    @staticmethod
    @numba.jit(nopython=True, parallel=True)
    def _compiled_convolution(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
        """JIT-compiled convolution implementation."""
        h, w = image.shape
        kh, kw = kernel.shape
        
        # Calculate output dimensions
        oh = h - kh + 1
        ow = w - kw + 1
        output = np.zeros((oh, ow), dtype=image.dtype)
        
        # Perform convolution
        for i in numba.prange(oh):
            for j in range(ow):
                for ki in range(kh):
                    for kj in range(kw):
                        output[i, j] += image[i + ki, j + kj] * kernel[ki, kj]
        
        return output
    
    def execute(self, image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
        """Execute JIT-compiled convolution."""
        return self._compiled_convolution(image, kernel)
```

### Memory-Efficient Primitives

```python
class MemoryEfficientOperation(PrimitiveOperation):
    """Memory-efficient primitive for large data."""
    
    def __init__(self):
        super().__init__(
            name="efficient_transpose",
            description="Memory-efficient matrix transpose"
        )
        self.input_types = [np.ndarray]
        self.output_type = np.ndarray
    
    def execute(self, matrix: np.ndarray, block_size: int = 1024) -> np.ndarray:
        """Execute memory-efficient transpose."""
        if matrix.size < 1000000:  # Small matrices
            return matrix.T
        
        # Large matrices - use block transpose
        return self._block_transpose(matrix, block_size)
    
    def _block_transpose(self, matrix: np.ndarray, block_size: int) -> np.ndarray:
        """Transpose large matrix using block algorithm."""
        h, w = matrix.shape
        result = np.empty((w, h), dtype=matrix.dtype)
        
        for i in range(0, h, block_size):
            for j in range(0, w, block_size):
                # Process block
                block_h = min(block_size, h - i)
                block_w = min(block_size, w - j)
                
                block = matrix[i:i+block_h, j:j+block_w]
                result[j:j+block_w, i:i+block_h] = block.T
        
        return result
    
    def estimate_memory_usage(self, matrix: np.ndarray) -> int:
        """Estimate memory usage."""
        return matrix.nbytes * 2  # Input + output
```

## Testing and Validation

### Comprehensive Test Suite

```python
class PrimitiveTestSuite:
    """Comprehensive test suite for primitives."""
    
    def __init__(self, primitive: PrimitiveOperation):
        self.primitive = primitive
    
    def run_all_tests(self) -> dict:
        """Run all tests and return results."""
        results = {}
        
        try:
            results['functionality'] = self._test_functionality()
            results['performance'] = self._test_performance()
            results['edge_cases'] = self._test_edge_cases()
            results['memory_usage'] = self._test_memory_usage()
            results['thread_safety'] = self._test_thread_safety()
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def _test_functionality(self) -> dict:
        """Test basic functionality."""
        test_cases = self._generate_test_cases()
        results = {}
        
        for name, inputs, expected in test_cases:
            try:
                actual = self.primitive.execute(*inputs)
                results[name] = {
                    'passed': self._compare_results(actual, expected),
                    'actual': actual,
                    'expected': expected
                }
            except Exception as e:
                results[name] = {'error': str(e)}
        
        return results
    
    def _test_performance(self) -> dict:
        """Test performance characteristics."""
        import time
        
        test_data = self._generate_performance_data()
        times = []
        
        for data in test_data:
            start_time = time.time()
            self.primitive.execute(*data)
            execution_time = time.time() - start_time
            times.append(execution_time)
        
        return {
            'min_time': min(times),
            'max_time': max(times),
            'avg_time': sum(times) / len(times),
            'total_time': sum(times)
        }
    
    def _test_edge_cases(self) -> dict:
        """Test edge cases and error conditions."""
        edge_cases = [
            ('empty_input', []),
            ('null_input', [None]),
            ('wrong_type', ['invalid_type']),
            ('out_of_bounds', [999999])
        ]
        
        results = {}
        for name, inputs in edge_cases:
            try:
                result = self.primitive.execute(*inputs)
                results[name] = {'unexpected_success': result}
            except Exception as e:
                results[name] = {'expected_error': type(e).__name__}
        
        return results
```

## Documentation and Discovery

### Auto-Generated Documentation

```python
def generate_primitive_documentation(primitive: PrimitiveOperation) -> str:
    """Generate comprehensive documentation for primitive."""
    
    metadata = primitive.get_metadata()
    
    doc = f"""
# {metadata['name']} - {metadata['description']}

## Overview
{metadata.get('overview', 'No overview available')}

## Input Types
{', '.join(metadata.get('input_types', []))}

## Output Type
{metadata.get('output_type', 'Unknown')}

## Usage Examples
"""
    
    for example in metadata.get('examples', []):
        doc += f"""
### {example['description']}
```python
{example['code']}
```
"""
    
    if 'performance_notes' in metadata:
        doc += f"""
## Performance Notes
{metadata['performance_notes']}
"""
    
    return doc
```

### Primitive Discovery Tools

```python
def discover_available_primitives() -> dict:
    """Discover all available primitives with metadata."""
    
    primitives = {}
    
    for namespace in PrimitiveRegistry.get_namespaces():
        namespace_primitives = PrimitiveRegistry.list_primitives(namespace)
        
        for name, primitive in namespace_primitives.items():
            full_name = f"{namespace}.{name}"
            primitives[full_name] = {
                'primitive': primitive,
                'metadata': primitive.get_metadata(),
                'namespace': namespace,
                'available': primitive.is_available() if hasattr(primitive, 'is_available') else True
            }
    
    return primitives

def generate_primitive_catalog() -> str:
    """Generate catalog of all available primitives."""
    
    primitives = discover_available_primitives()
    
    catalog = "# VoxLogicA Primitive Catalog\n\n"
    
    by_category = {}
    for name, info in primitives.items():
        category = info['metadata'].get('category', 'general')
        if category not in by_category:
            by_category[category] = []
        by_category[category].append((name, info))
    
    for category, primitive_list in sorted(by_category.items()):
        catalog += f"## {category.title()}\n\n"
        
        for name, info in sorted(primitive_list):
            catalog += f"- **{name}**: {info['metadata']['description']}\n"
        
        catalog += "\n"
    
    return catalog
```
