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
    subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])


def install_requirements():
    pip_path = os.path.join(VENV_DIR, "bin", "pip")
    if not os.path.isfile(REQUIREMENTS):
        print(f"{REQUIREMENTS} not found, skipping install.")
        return
    print(f"Installing dependencies from {REQUIREMENTS}...")
    subprocess.check_call([pip_path, "install", "-r", REQUIREMENTS])


def main():
    if venv_exists():
        print(f"Virtual environment already exists in {VENV_DIR}.")
    else:
        create_venv()
        install_requirements()
        print("Setup complete.")


if __name__ == "__main__":
    main()
