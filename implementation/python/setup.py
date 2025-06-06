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
    python_requires=">=3.9",
)
