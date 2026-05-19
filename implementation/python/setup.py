from setuptools import find_packages, setup

from voxlogica import __version__

setup(
    name="voxlogica",
    version=__version__,
    packages=find_packages(),
    install_requires=[
        "lark>=1.2.0",
    ],
    entry_points={
        "console_scripts": [
            "voxlogica=voxlogica.main:main",
        ],
    },
    python_requires=">=3.10",
)
