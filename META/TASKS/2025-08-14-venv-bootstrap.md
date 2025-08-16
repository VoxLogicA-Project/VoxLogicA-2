# Task: Ensure venv is prepared on each `./voxlogica` run

- Date: 2025-08-14
- Context: User requested the root `voxlogica` script to always prepare the virtual environment.
- Change: Updated `voxlogica` to invoke `bootstrap.py` on every run (idempotent), instead of only when `.venv` is missing.
- Files Changed:
  - `voxlogica`: Always runs `python3 bootstrap.py` before activation.
- Notes: `bootstrap.py` maintains existing behavior (creates venv if absent, installs requirements). No other behavior changes.
- Verification: Quick smoke attempt via `./voxlogica version`; dependencies are managed by existing venv according to environment.
