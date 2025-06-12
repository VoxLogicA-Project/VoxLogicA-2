"""
Directory loading as Dask bag for dataset processing
"""
import dask.bag as db
from pathlib import Path

def execute(**kwargs) -> db.Bag:
    """
    Load directory contents as Dask bag of file paths
    
    Args:
        **kwargs: VoxLogicA argument convention:
            '0': directory_path (str) - Path to directory to scan
            '1': pattern (str, optional) - Glob pattern (default: "*")
            
    Returns:
        dask.bag.Bag: Bag containing absolute file paths as strings
    """
    directory_path = kwargs['0']
    pattern = kwargs.get('1', '*')
    
    if not isinstance(directory_path, str):
        raise TypeError(f"Directory path must be string, got {type(directory_path)}")
    
    path = Path(directory_path)
    if not path.exists():
        raise ValueError(f"Directory does not exist: {directory_path}")
    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {directory_path}")

    # TODO: here we could use iterators / streams for more "lazy" loading
    files = [str(f.absolute()) for f in path.glob(pattern) if f.is_file()]
    npartitions = max(1, min(len(files) // 100, 10))
    
    return db.from_sequence(files, npartitions=npartitions)
