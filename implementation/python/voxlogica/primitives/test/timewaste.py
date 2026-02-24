"""
Timewaste primitive for VoxLogicA-2

Performs expensive numpy computations including matrix operations, eigenvalue decomposition,
and iterative mathematical operations. This is intentionally CPU-intensive and is used for
testing purposes where you want to consume CPU cycles with two numeric arguments.
"""

import numpy as np

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    """
    Execute timewaste computation (expensive numpy operations)
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': size_factor, '1': iterations} where size_factor
                          controls matrix size and iterations controls computational intensity
        
    Returns:
        A number representing the result of expensive computations
        
    Raises:
        ValueError: If arguments are invalid or missing
    """
    try:
        # Get the arguments
        if '0' not in kwargs:
            raise ValueError("Timewaste requires two arguments: size_factor and iterations")
        if '1' not in kwargs:
            raise ValueError("Timewaste requires two arguments: size_factor and iterations")
        
        size_factor = kwargs['0']
        iterations = kwargs['1']
        
        # Convert to numeric types if possible
        if isinstance(size_factor, float) and size_factor.is_integer():
            size_factor = int(size_factor)
        if isinstance(iterations, float) and iterations.is_integer():
            iterations = int(iterations)
        
        if not isinstance(size_factor, (int, float)):
            raise ValueError("Size factor must be numeric")
        if not isinstance(iterations, (int, float)):
            raise ValueError("Iterations must be numeric")
        
        # Ensure reasonable bounds
        size_factor = max(1, min(1000, abs(size_factor)))  # Clamp to reasonable range
        iterations = max(1, min(10000, abs(int(iterations))))  # Clamp iterations
        
        # Calculate matrix size based on size_factor
        matrix_size = max(10, int(size_factor * 5))  # At least 10x10, scales with size_factor
        
        # CPU-intensive numpy computations
        result = 0.0
        
        for i in range(iterations):
            # Create random matrices for expensive operations
            np.random.seed(i + int(size_factor))  # Deterministic but varying seed
            
            # Generate complex matrices
            A = np.random.randn(matrix_size, matrix_size) + 1j * np.random.randn(matrix_size, matrix_size)
            B = np.random.randn(matrix_size, matrix_size) + 1j * np.random.randn(matrix_size, matrix_size)
            
            # Expensive matrix operations
            # 1. Matrix multiplication
            C = np.dot(A, B)
            
            # 2. Eigenvalue decomposition (very expensive)
            try:
                eigenvals = np.linalg.eigvals(C)
                eigen_sum = np.sum(np.real(eigenvals))
            except np.linalg.LinAlgError:
                eigen_sum = 0.0
            
            # 3. Matrix inversion (expensive and numerically intensive)
            try:
                # Add small diagonal to ensure invertibility
                regularized = C + np.eye(matrix_size) * 1e-6
                inv_C = np.linalg.inv(regularized)
                inv_trace = np.trace(inv_C).real
            except np.linalg.LinAlgError:
                inv_trace = 0.0
            
            # 4. SVD decomposition (very expensive)
            try:
                U, s, Vh = np.linalg.svd(C)
                svd_sum = np.sum(s)
            except np.linalg.LinAlgError:
                svd_sum = 0.0
            
            # 5. Additional CPU-intensive operations
            # Fourier transform
            fft_result = np.fft.fft2(np.real(C))
            fft_magnitude = np.sum(np.abs(fft_result))
            
            # Mathematical series computation
            series_sum = 0.0
            for j in range(min(1000, matrix_size * 10)):
                series_sum += np.sin(j * size_factor / 1000.0) * np.cos(j * iterations / 1000.0)
            
            # Accumulate results
            iteration_result = (eigen_sum + inv_trace + svd_sum + 
                              fft_magnitude / 1000.0 + series_sum / 100.0)
            result += iteration_result
        
        # Return final result as a real number
        final_result = float(np.real(result / iterations))
        return final_result
        
    except Exception as e:
        raise ValueError(f"Timewaste computation failed: {e}") from e


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="timewaste",
    namespace="test",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("test.timewaste", kind="scalar"),
    kernel_name="test.timewaste",
    description="CPU-intensive numeric workload for stress testing",
)
