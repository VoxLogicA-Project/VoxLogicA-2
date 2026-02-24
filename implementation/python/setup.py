from setuptools import setup, find_packages

# Import version from the package
from voxlogica.version import __version__

setup(
    name="voxlogica",
    version=__version__,
    packages=find_packages(),
    install_requires=[
        "lark>=1.1.5",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "typer>=0.9.0",
        "pydantic>=2.0.0",
    ],
    entry_points={
        "console_scripts": [
            "voxlogica=voxlogica.main:app",
        ],
    },
    extras_require={
        "test": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "pytest-mock>=3.10.0",
            "hypothesis>=6.0.0",
            "dask[complete]>=2023.5.0",
            "ruff>=0.7.0",
            "mypy>=1.10.0",
        ],
    },
    python_requires=">=3.9",
)
