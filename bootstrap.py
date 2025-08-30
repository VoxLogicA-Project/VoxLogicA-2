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
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create virtual environment: {e}")
        print("\nTo fix this on Debian/Ubuntu systems, install python3-venv:")
        print(
            f"  sudo apt install python{sys.version_info.major}.{sys.version_info.minor}-venv"
        )
        print("\nOr install the python3-venv package:")
        print("  sudo apt install python3-venv")
        print("\nThen run this script again.")
        return False


def install_requirements():
    if not os.path.isfile(REQUIREMENTS):
        print(f"{REQUIREMENTS} not found, skipping install.")
        return

    venv_python = os.path.join(VENV_DIR, "bin", "python")

    # Check if we already have lark installed as an indicator that deps are installed
    try:
        result = subprocess.run(
            [venv_python, "-c", "import lark"], capture_output=True, text=True
        )
        if result.returncode == 0:
            print("Dependencies already installed, skipping reinstall.")
            return
    except FileNotFoundError:
        pass

    print(f"Installing dependencies from {REQUIREMENTS}...")
    subprocess.check_call([venv_python, "-m", "pip", "install", "-r", REQUIREMENTS])


def main():
    if venv_exists():
        print(f"Virtual environment already exists in {VENV_DIR}.")
        install_requirements()
    else:
        if create_venv():
            install_requirements()
            print("Setup complete.")
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
