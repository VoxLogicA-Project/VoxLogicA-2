# primitives/ - Extensible Primitive Operation Libraries

## Purpose

The `primitives/` package provides extensible libraries of primitive operations that can be invoked from VoxLogicA programs. It implements a plugin-based architecture allowing for domain-specific operations to be easily added and integrated into the VoxLogicA execution engine.

## Architecture

### Core Components

#### 1. Primitive Base Classes
- **PrimitiveOperation**: Base class for all primitive operations
- **ImagePrimitive**: Specialized base for image processing operations  
- **ComputationPrimitive**: Base for general computational operations
- **IOPrimitive**: Base for input/output operations

#### 2. Library Organization
- **default/**: Core built-in primitive operations
- **simpleitk/**: Medical image processing primitives using SimpleITK
- **test/**: Testing and validation primitives
- **Custom Libraries**: Domain-specific primitive collections

#### 3. Registration System
- **Automatic Discovery**: Primitives are automatically discovered and registered
- **Namespace Management**: Primitives can be organized in hierarchical namespaces
- **Version Management**: Support for multiple versions of primitive libraries

### Key Classes and Interfaces

#### `PrimitiveOperation` Base Class
```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class PrimitiveOperation(ABC):
    """Base class for all VoxLogicA primitive operations."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.input_types: List[type] = []
        self.output_type: Optional[type] = None
    
    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the primitive operation."""
        pass
    
    def validate_inputs(self, *args) -> bool:
        """Validate input arguments match expected types."""
        if len(args) != len(self.input_types):
            return False
        
        for arg, expected_type in zip(args, self.input_types):
            if not isinstance(arg, expected_type):
                return False
        
        return True
    
    def get_metadata(self) -> Dict[str, Any]:
        """Get operation metadata for documentation and introspection."""
        return {
            'name': self.name,
            'description': self.description,
            'input_types': [t.__name__ for t in self.input_types],
            'output_type': self.output_type.__name__ if self.output_type else None,
            'version': getattr(self, 'version', '1.0'),
            'category': getattr(self, 'category', 'general')
        }
```

#### Primitive Registration
```python
class PrimitiveRegistry:
    """Registry for primitive operations with namespace support."""
    
    _primitives: Dict[str, PrimitiveOperation] = {}
    _namespaces: Dict[str, Dict[str, PrimitiveOperation]] = {}
    
    @classmethod
    def register(
        cls,
        primitive: PrimitiveOperation,
        namespace: str = "default"
    ) -> None:
        """Register a primitive operation in specified namespace."""
        
        if namespace not in cls._namespaces:
            cls._namespaces[namespace] = {}
        
        full_name = f"{namespace}.{primitive.name}"
        cls._primitives[full_name] = primitive
        cls._namespaces[namespace][primitive.name] = primitive
    
    @classmethod
    def get_primitive(cls, name: str, namespace: str = "default") -> Optional[PrimitiveOperation]:
        """Get primitive operation by name and namespace."""
        
        # Try full name first
        if name in cls._primitives:
            return cls._primitives[name]
        
        # Try namespace.name
        full_name = f"{namespace}.{name}"
        if full_name in cls._primitives:
            return cls._primitives[full_name]
        
        # Try just name in default namespace
        if namespace in cls._namespaces and name in cls._namespaces[namespace]:
            return cls._namespaces[namespace][name]
        
        return None
    
    @classmethod
    def list_primitives(cls, namespace: Optional[str] = None) -> Dict[str, PrimitiveOperation]:
        """List all primitives in specified namespace or all namespaces."""
        
        if namespace is None:
            return cls._primitives.copy()
        else:
            return cls._namespaces.get(namespace, {}).copy()
```

## Default Primitive Library

### Basic Operations
```python
# default/basic.py

class AddOperation(PrimitiveOperation):
    """Add two numbers."""
    
    def __init__(self):
        super().__init__("add", "Add two numbers")
        self.input_types = [float, float]
        self.output_type = float
    
    def execute(self, a: float, b: float) -> float:
        return a + b

class MultiplyOperation(PrimitiveOperation):
    """Multiply two numbers."""
    
    def __init__(self):
        super().__init__("multiply", "Multiply two numbers")
        self.input_types = [float, float]
        self.output_type = float
    
    def execute(self, a: float, b: float) -> float:
        return a * b

class ConcatenateOperation(PrimitiveOperation):
    """Concatenate two strings."""
    
    def __init__(self):
        super().__init__("concat", "Concatenate two strings")
        self.input_types = [str, str]
        self.output_type = str
    
    def execute(self, a: str, b: str) -> str:
        return a + b
```

### Collection Operations
```python
# default/collections.py

class RangeOperation(PrimitiveOperation):
    """Generate range of numbers."""
    
    def __init__(self):
        super().__init__("range", "Generate range of numbers")
        self.input_types = [int, int]
        self.output_type = list
    
    def execute(self, start: int, end: int) -> List[int]:
        return list(range(start, end))

class MapOperation(PrimitiveOperation):
    """Apply function to all elements in a list."""
    
    def __init__(self):
        super().__init__("map", "Apply function to list elements")
        self.input_types = [callable, list]
        self.output_type = list
    
    def execute(self, func: callable, items: list) -> list:
        return [func(item) for item in items]

class FilterOperation(PrimitiveOperation):
    """Filter list elements using predicate."""
    
    def __init__(self):
        super().__init__("filter", "Filter list using predicate")
        self.input_types = [callable, list]
        self.output_type = list
    
    def execute(self, predicate: callable, items: list) -> list:
        return [item for item in items if predicate(item)]
```

## SimpleITK Medical Imaging Library

### Image Loading and Saving
```python
# simpleitk/io.py

import SimpleITK as sitk
from typing import Union
from pathlib import Path

class LoadImageOperation(PrimitiveOperation):
    """Load medical image using SimpleITK."""
    
    def __init__(self):
        super().__init__("load_image", "Load medical image from file")
        self.input_types = [str]
        self.output_type = sitk.Image
        self.category = "io"
    
    def execute(self, filename: str) -> sitk.Image:
        """Load image from file."""
        try:
            return sitk.ReadImage(str(filename))
        except Exception as e:
            raise RuntimeError(f"Failed to load image {filename}: {e}")

class SaveImageOperation(PrimitiveOperation):
    """Save medical image using SimpleITK."""
    
    def __init__(self):
        super().__init__("save_image", "Save medical image to file") 
        self.input_types = [sitk.Image, str]
        self.output_type = type(None)
        self.category = "io"
    
    def execute(self, image: sitk.Image, filename: str) -> None:
        """Save image to file."""
        try:
            sitk.WriteImage(image, str(filename))
        except Exception as e:
            raise RuntimeError(f"Failed to save image {filename}: {e}")
```

### Image Processing Operations
```python
# simpleitk/filters.py

class GaussianBlurOperation(PrimitiveOperation):
    """Apply Gaussian blur to image."""
    
    def __init__(self):
        super().__init__("gaussian_blur", "Apply Gaussian blur filter")
        self.input_types = [sitk.Image, float]
        self.output_type = sitk.Image
        self.category = "filter"
    
    def execute(self, image: sitk.Image, sigma: float) -> sitk.Image:
        """Apply Gaussian blur with specified sigma."""
        return sitk.SmoothingRecursiveGaussian(image, sigma)

class ThresholdOperation(PrimitiveOperation):
    """Apply binary threshold to image."""
    
    def __init__(self):
        super().__init__("threshold", "Apply binary threshold")
        self.input_types = [sitk.Image, float, float]
        self.output_type = sitk.Image
        self.category = "segmentation"
    
    def execute(self, image: sitk.Image, lower: float, upper: float) -> sitk.Image:
        """Apply binary threshold between lower and upper values."""
        return sitk.BinaryThreshold(image, lower, upper, 1, 0)

class ConnectedComponentsOperation(PrimitiveOperation):
    """Find connected components in binary image."""
    
    def __init__(self):
        super().__init__("connected_components", "Find connected components")
        self.input_types = [sitk.Image]
        self.output_type = sitk.Image
        self.category = "analysis"
    
    def execute(self, binary_image: sitk.Image) -> sitk.Image:
        """Find connected components in binary image."""
        return sitk.ConnectedComponent(binary_image)
```

### Statistical Operations
```python
# simpleitk/statistics.py

class ImageStatisticsOperation(PrimitiveOperation):
    """Compute image statistics."""
    
    def __init__(self):
        super().__init__("image_stats", "Compute image statistics")
        self.input_types = [sitk.Image]
        self.output_type = dict
        self.category = "analysis"
    
    def execute(self, image: sitk.Image) -> Dict[str, float]:
        """Compute comprehensive image statistics."""
        
        stats_filter = sitk.StatisticsImageFilter()
        stats_filter.Execute(image)
        
        return {
            'mean': stats_filter.GetMean(),
            'std': stats_filter.GetSigma(),
            'min': stats_filter.GetMinimum(),
            'max': stats_filter.GetMaximum(),
            'sum': stats_filter.GetSum(),
            'variance': stats_filter.GetVariance()
        }

class LabelStatisticsOperation(PrimitiveOperation):
    """Compute statistics for labeled regions."""
    
    def __init__(self):
        super().__init__("label_stats", "Compute label statistics")
        self.input_types = [sitk.Image, sitk.Image]
        self.output_type = dict
        self.category = "analysis"
    
    def execute(self, image: sitk.Image, labels: sitk.Image) -> Dict[int, Dict[str, float]]:
        """Compute statistics for each label in the image."""
        
        stats_filter = sitk.LabelStatisticsImageFilter()
        stats_filter.Execute(image, labels)
        
        results = {}
        for label in stats_filter.GetLabels():
            results[label] = {
                'mean': stats_filter.GetMean(label),
                'std': stats_filter.GetSigma(label),
                'min': stats_filter.GetMinimum(label),
                'max': stats_filter.GetMaximum(label),
                'count': stats_filter.GetCount(label),
                'region_area': stats_filter.GetRegion(label)
            }
        
        return results
```

## Implementation Details

### Automatic Discovery and Registration

```python
# __init__.py
import importlib
import pkgutil
from pathlib import Path

def discover_and_register_primitives(package_path: Path, namespace: str = "default"):
    """Automatically discover and register primitive operations."""
    
    for module_finder, name, ispkg in pkgutil.iter_modules([str(package_path)]):
        if not ispkg:  # Only process Python files, not subdirectories
            try:
                module = importlib.import_module(f"{package_path.name}.{name}")
                
                # Find primitive classes in module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    
                    if (isinstance(attr, type) and 
                        issubclass(attr, PrimitiveOperation) and 
                        attr is not PrimitiveOperation):
                        
                        # Instantiate and register primitive
                        primitive = attr()
                        PrimitiveRegistry.register(primitive, namespace)
                        
                        logger.info(f"Registered primitive: {namespace}.{primitive.name}")
                        
            except Exception as e:
                logger.warning(f"Failed to load primitive module {name}: {e}")

# Auto-discover primitives on import
def initialize_primitives():
    """Initialize all primitive libraries."""
    
    current_dir = Path(__file__).parent
    
    # Register default primitives
    default_path = current_dir / "default"
    if default_path.exists():
        discover_and_register_primitives(default_path, "default")
    
    # Register SimpleITK primitives if available
    try:
        import SimpleITK
        simpleitk_path = current_dir / "simpleitk"
        if simpleitk_path.exists():
            discover_and_register_primitives(simpleitk_path, "simpleitk")
    except ImportError:
        logger.info("SimpleITK not available, skipping SimpleITK primitives")
    
    # Register test primitives in development mode
    if __debug__:
        test_path = current_dir / "test"
        if test_path.exists():
            discover_and_register_primitives(test_path, "test")

# Initialize on module import
initialize_primitives()
```

### Dynamic Primitive Loading

```python
def load_primitive_library(library_path: Union[str, Path], namespace: str) -> int:
    """Dynamically load primitive library from file system."""
    
    library_path = Path(library_path)
    loaded_count = 0
    
    if library_path.is_file() and library_path.suffix == ".py":
        # Load single Python file
        spec = importlib.util.spec_from_file_location("custom_primitives", library_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Register primitives from module
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and 
                issubclass(attr, PrimitiveOperation) and 
                attr is not PrimitiveOperation):
                
                primitive = attr()
                PrimitiveRegistry.register(primitive, namespace)
                loaded_count += 1
    
    elif library_path.is_dir():
        # Load all Python files in directory
        discover_and_register_primitives(library_path, namespace)
        loaded_count = len(PrimitiveRegistry.list_primitives(namespace))
    
    return loaded_count
```

## Dependencies

### Internal Dependencies
- `voxlogica.features` - Integration with feature registry
- `voxlogica.storage` - Caching of operation results

### External Dependencies
- `SimpleITK` - Medical image processing (optional)
- `numpy` - Numerical operations (optional)
- `scipy` - Scientific computing (optional)
- `scikit-image` - Image processing (optional)

## Usage Examples

### Using Built-in Primitives
```python
from voxlogica.primitives import PrimitiveRegistry

# Get and execute basic math operations
add_op = PrimitiveRegistry.get_primitive("add", "default")
result = add_op.execute(5.0, 3.0)  # Returns 8.0

multiply_op = PrimitiveRegistry.get_primitive("multiply", "default")
result = multiply_op.execute(4.0, 2.5)  # Returns 10.0

# List all available primitives
all_primitives = PrimitiveRegistry.list_primitives()
for name, primitive in all_primitives.items():
    print(f"{name}: {primitive.description}")
```

### Using SimpleITK Primitives
```python
# Load and process medical image
load_op = PrimitiveRegistry.get_primitive("load_image", "simpleitk")
blur_op = PrimitiveRegistry.get_primitive("gaussian_blur", "simpleitk")
save_op = PrimitiveRegistry.get_primitive("save_image", "simpleitk")

# Execute image processing pipeline
image = load_op.execute("input.nii.gz")
blurred = blur_op.execute(image, 2.0)  # sigma = 2.0
save_op.execute(blurred, "output.nii.gz")
```

### Creating Custom Primitives
```python
class CustomAnalysisOperation(PrimitiveOperation):
    """Custom analysis operation for specific domain."""
    
    def __init__(self):
        super().__init__("custom_analysis", "Perform custom domain analysis")
        self.input_types = [dict, float]
        self.output_type = dict
        self.category = "analysis"
        self.version = "1.0"
    
    def execute(self, data: dict, threshold: float) -> dict:
        """Perform custom analysis on data."""
        
        results = {}
        for key, values in data.items():
            if isinstance(values, list):
                filtered_values = [v for v in values if v > threshold]
                results[key] = {
                    'count': len(filtered_values),
                    'average': sum(filtered_values) / len(filtered_values) if filtered_values else 0,
                    'max': max(filtered_values) if filtered_values else 0
                }
        
        return results

# Register custom primitive
custom_op = CustomAnalysisOperation()
PrimitiveRegistry.register(custom_op, "custom")

# Use custom primitive
result = custom_op.execute(
    {"group1": [1, 5, 8, 3], "group2": [2, 9, 4, 7]},
    5.0
)
```

### Primitive Composition
```python
def create_image_processing_pipeline() -> List[PrimitiveOperation]:
    """Create composed image processing pipeline."""
    
    return [
        PrimitiveRegistry.get_primitive("load_image", "simpleitk"),
        PrimitiveRegistry.get_primitive("gaussian_blur", "simpleitk"),
        PrimitiveRegistry.get_primitive("threshold", "simpleitk"),
        PrimitiveRegistry.get_primitive("connected_components", "simpleitk"),
        PrimitiveRegistry.get_primitive("label_stats", "simpleitk"),
        PrimitiveRegistry.get_primitive("save_image", "simpleitk")
    ]

def execute_pipeline(pipeline: List[PrimitiveOperation], initial_data: Any) -> Any:
    """Execute primitive pipeline sequentially."""
    
    current_data = initial_data
    
    for i, primitive in enumerate(pipeline):
        try:
            if i == 0:
                # First operation - single input
                current_data = primitive.execute(current_data)
            else:
                # Subsequent operations may need additional parameters
                # This would need more sophisticated parameter management
                current_data = primitive.execute(current_data)
        
        except Exception as e:
            raise RuntimeError(f"Pipeline failed at step {i} ({primitive.name}): {e}")
    
    return current_data
```

## Performance Considerations

### Operation Caching
- **Result Memoization**: Expensive operations are cached based on input parameters
- **Intermediate Results**: Pipeline intermediate results can be cached
- **Content-Addressed Storage**: Results stored using content hashes for deduplication

### Parallel Execution
- **Thread Safety**: Primitive operations are designed to be thread-safe
- **Batch Processing**: Multiple operations can be batched for efficiency
- **Resource Management**: Memory and CPU usage is monitored and controlled

### Optimization Strategies
- **Lazy Loading**: Primitive libraries are loaded only when needed
- **JIT Compilation**: Frequently used operations can be compiled for speed
- **GPU Acceleration**: Operations can be accelerated using GPU when available

## Integration Points

### With Execution Engine
Primitives are integrated into the execution engine for operation dispatch:

```python
# In execution.py
def execute_primitive_operation(operation: Operation) -> Any:
    """Execute primitive operation with caching and error handling."""
    
    primitive = PrimitiveRegistry.get_primitive(operation.primitive_name)
    if not primitive:
        raise RuntimeError(f"Unknown primitive: {operation.primitive_name}")
    
    # Validate inputs
    args = resolve_operation_arguments(operation.arguments)
    if not primitive.validate_inputs(*args):
        raise TypeError(f"Invalid arguments for {operation.primitive_name}")
    
    # Check cache
    cache_key = operation.content_hash
    cached_result = storage.get(cache_key)
    if cached_result is not None:
        return cached_result
    
    # Execute and cache
    result = primitive.execute(*args)
    storage.put(cache_key, result)
    
    return result
```

### With Feature System
Primitives can be exposed as CLI/API features:

```python
# Auto-register primitives as features
for name, primitive in PrimitiveRegistry.list_primitives().items():
    feature = Feature(
        name=f"primitive_{name}",
        description=primitive.description,
        handler=primitive.execute,
        cli_options=generate_cli_options(primitive)
    )
    FeatureRegistry.register(feature)
```

### With Testing Framework
Primitives include comprehensive testing infrastructure:

```python
# test/test_primitives.py
def test_all_primitives():
    """Test all registered primitives with sample data."""
    
    for name, primitive in PrimitiveRegistry.list_primitives().items():
        test_data = generate_test_data(primitive)
        
        try:
            result = primitive.execute(*test_data)
            assert result is not None, f"Primitive {name} returned None"
            
            if primitive.output_type:
                assert isinstance(result, primitive.output_type), \
                    f"Primitive {name} returned wrong type"
        
        except Exception as e:
            pytest.fail(f"Primitive {name} failed: {e}")
```
