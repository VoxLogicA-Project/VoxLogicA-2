import os
import subprocess
import sys
import shutil

VENV_DIR = ".venv"
REQUIREMENTS = "implementation/python/requirements.txt"


def venv_exists():
    return os.path.isdir(VENV_DIR) and os.path.isfile(
        os.path.join(VENV_DIR, "bin", "activate")
    )


def venv_python_version(venv_python):
    try:
        out = subprocess.check_output([venv_python, "-c", "import sys; print(tuple(sys.version_info[:2]))"], text=True)
        return eval(out.strip())
    except Exception:
        return None


def create_venv():
    python_for_venv = find_suitable_python()
    print(f"Creating virtual environment in {VENV_DIR} using {python_for_venv}...")
    try:
        subprocess.check_call([python_for_venv, "-m", "venv", VENV_DIR])
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create virtual environment: {e}")
        print("\nTo fix this on Debian/Ubuntu systems, install a recent Python and the venv module, for example:")
        print("  sudo apt install python3.10 python3.10-venv  # or python3.11/python3.12 as available")
        print("\nThen run this script again with that python on PATH (e.g. python3.10).")
        return False


def find_suitable_python(min_major=3, min_minor=10):
    """Return path to a Python interpreter with at least min_major.min_minor.

    Preference order: current sys.executable, then common python3.x names on PATH.
    If none found, exit with a helpful message.
    """
    def is_suitable(path):
        try:
            out = subprocess.check_output([path, "-c", "import sys; print(sys.version_info[:2])"], text=True)
            # out looks like: (3, 11)
            tup = eval(out.strip())
            return tup[0] > min_major or (tup[0] == min_major and tup[1] >= min_minor)
        except Exception:
            return False

    # First try current interpreter
    try:
        if is_suitable(sys.executable):
            return sys.executable
    except Exception:
        pass

    # Search common python executables
    candidates = ["python3.13", "python3.12", "python3.11", "python3.10", "python3.9"]
    for name in candidates:
        p = shutil.which(name)
        if p and is_suitable(p):
            return p

    print(f"No suitable Python >= {min_major}.{min_minor} found on PATH.")
    print("Please install Python 3.10+ and ensure e.g. 'python3.10' is on your PATH, or run this script with a newer python:")
    print("  python3.10 bootstrap.py")
    sys.exit(2)


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
    # Make sure pip, setuptools and wheel are up to date in the venv to improve build support
    try:
        subprocess.check_call([venv_python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    except subprocess.CalledProcessError:
        print("Failed to upgrade pip/setuptools/wheel in the virtualenv; continuing but builds may fail.")

    try:
        subprocess.check_call([venv_python, "-m", "pip", "install", "-r", REQUIREMENTS])
    except subprocess.CalledProcessError as e:
        print("pip install failed. See output above for details.")
        print("If this is due to Python version incompatibility, ensure the venv was created with Python >= 3.10.")
        raise


def main():
    if venv_exists():
        venv_python = os.path.join(VENV_DIR, "bin", "python")
        vver = venv_python_version(venv_python)
        if vver is None:
            print(f"Couldn't determine Python version in existing {VENV_DIR}, recreating venv.")
            shutil.rmtree(VENV_DIR)
            if not create_venv():
                sys.exit(1)
        else:
            maj, mino = vver
            if maj < 3 or (maj == 3 and mino < 10):
                print(f"Existing virtualenv Python {maj}.{mino} is too old. Recreating with Python >=3.10...")
                shutil.rmtree(VENV_DIR)
                if not create_venv():
                    sys.exit(1)
            else:
                print(f"Virtual environment already exists in {VENV_DIR} (Python {maj}.{mino}).")
        install_requirements()
        print("Setup complete.")
    else:
        if create_venv():
            install_requirements()
            print("Setup complete.")
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
