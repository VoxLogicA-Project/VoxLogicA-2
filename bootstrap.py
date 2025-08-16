import os
import subprocess
import sys

VENV_DIR = ".venv"
REQUIREMENTS = "implementation/python/requirements.txt"


def venv_exists():
    return os.path.isdir(VENV_DIR) and os.path.isfile(
        os.path.join(VENV_DIR, "bin", "activate")
    )


def create_venv():
    print(f"Creating virtual environment in {VENV_DIR}...")
    try:
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
    except subprocess.CalledProcessError:
        return create_minimal_venv()


def create_minimal_venv():
    """Create a minimal virtual environment structure when ensurepip is not available"""
    print("Standard venv creation failed, creating minimal structure...")

    # Create directory structure
    os.makedirs(os.path.join(VENV_DIR, "bin"), exist_ok=True)
    os.makedirs(
        os.path.join(VENV_DIR, "lib", "python3.12", "site-packages"), exist_ok=True
    )
    os.makedirs(os.path.join(VENV_DIR, "include"), exist_ok=True)

    # Create python symlinks
    python_exe = os.path.join(VENV_DIR, "bin", "python")
    python3_exe = os.path.join(VENV_DIR, "bin", "python3")
    python312_exe = os.path.join(VENV_DIR, "bin", "python3.12")

    if not os.path.exists(python3_exe):
        os.symlink("/usr/bin/python3", python3_exe)
    if not os.path.exists(python_exe):
        os.symlink("python3", python_exe)
    if not os.path.exists(python312_exe):
        os.symlink("python3", python312_exe)

    # Create lib64 -> lib symlink
    lib64 = os.path.join(VENV_DIR, "lib64")
    if not os.path.exists(lib64):
        os.symlink("lib", lib64)

    # Create a minimal activate script
    activate_script = os.path.join(VENV_DIR, "bin", "activate")
    with open(activate_script, "w") as f:
        f.write(
            f"""#!/bin/bash
# This is a minimal activate script for VoxLogicA
export VIRTUAL_ENV="{os.path.abspath(VENV_DIR)}"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export PYTHONPATH="$VIRTUAL_ENV/lib/python3.12/site-packages:$PYTHONPATH"
unset PYTHONHOME
"""
        )

    # Create pyvenv.cfg
    pyvenv_cfg = os.path.join(VENV_DIR, "pyvenv.cfg")
    with open(pyvenv_cfg, "w") as f:
        f.write(
            f"""home = /usr/bin
include-system-site-packages = false
version = 3.12
executable = /usr/bin/python3.12
command = {sys.executable} -m venv {VENV_DIR}
"""
        )

    return True


def install_requirements():
    if not os.path.isfile(REQUIREMENTS):
        print(f"{REQUIREMENTS} not found, skipping install.")
        return

    venv_python = os.path.join(VENV_DIR, "bin", "python")
    site_packages = os.path.join(VENV_DIR, "lib", "python3.12", "site-packages")

    # Check if we already have lark installed as an indicator that deps are installed
    if os.path.exists(os.path.join(site_packages, "lark")):
        print("Dependencies already installed, skipping reinstall.")
        return

    print(f"Installing dependencies from {REQUIREMENTS} to {site_packages}...")
    try:
        # Try using the venv python first (if it has pip)
        subprocess.check_call([venv_python, "-m", "pip", "install", "-r", REQUIREMENTS])
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fall back to installing directly to site-packages with system python
        subprocess.check_call(
            [
                "/usr/bin/python3",
                "-m",
                "pip",
                "install",
                "--target",
                site_packages,
                "-r",
                REQUIREMENTS,
            ]
        )


def main():
    if venv_exists():
        print(f"Virtual environment already exists in {VENV_DIR}.")
        # Always ensure dependencies are installed/updated
        install_requirements()
    else:
        create_venv()
        install_requirements()
        print("Setup complete.")


if __name__ == "__main__":
    main()
