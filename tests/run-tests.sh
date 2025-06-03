#!/bin/bash
# Run VoxLogicA tests using the project's virtual environment

# Exit on error
set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$SCRIPT_DIR/.."
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_IMPL_DIR="$PROJECT_DIR/implementation/python"

# Function to print error message and exit
function error_exit {
    echo "ERROR: $1" >&2
    exit 1
}

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    error_exit "Virtual environment not found at $VENV_DIR. Please set up the virtual environment first."
fi

# Check if Python implementation directory exists
if [ ! -d "$PYTHON_IMPL_DIR" ]; then
    error_exit "Python implementation not found at $PYTHON_IMPL_DIR"
fi

# Activate the virtual environment
if [ -f "$VENV_DIR/bin/activate" ]; then
    # Unix/macOS
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    # Windows
    source "$VENV_DIR/Scripts/activate"
else
    error_exit "Could not find virtual environment activation script"
fi

# Install test requirements if not already installed
pip install -q -r "$PYTHON_IMPL_DIR/requirements-test.txt"

# Run pytest with the python implementation directory as the working directory
cd "$PYTHON_IMPL_DIR"
python -m pytest tests/ -v "$@"
