#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
VENV_DIR="$PROJECT_DIR/.venv"
REQ_TEST="$PROJECT_DIR/implementation/python/requirements-test.txt"
if [ -d "$VENV_DIR" ]; then
  if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
  elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
  fi
fi

if [ "${VOXLOGICA_SKIP_TEST_DEPS_INSTALL:-0}" != "1" ]; then
  python -m pip install -q -r "$REQ_TEST"
fi

cd "$PROJECT_DIR"

# Best-effort fetch of VoxLogicA-1 binary used by cross-version parity/perf tests.
if [ "${VOXLOGICA_FETCH_VOX1:-1}" = "1" ]; then
  python tests/fetch_vox1_binary.py --quiet >/dev/null 2>&1 || true
fi

REPORT_ROOT="${VOXLOGICA_TEST_REPORT_DIR:-$PROJECT_DIR/tests/reports}"
mkdir -p "$REPORT_ROOT/perf"
export VOXLOGICA_PERF_REPORT_DIR="${VOXLOGICA_PERF_REPORT_DIR:-$REPORT_ROOT/perf}"

PYTEST_ARGS=("$@")
if [ "${VOXLOGICA_DISABLE_AUTOREPORTS:-0}" != "1" ]; then
  PYTEST_ARGS+=(
    "--junitxml" "$REPORT_ROOT/junit.xml"
    "--cov=implementation/python/voxlogica"
    "--cov-report=xml:$REPORT_ROOT/coverage.xml"
    "--cov-report=term"
  )
fi

pytest "${PYTEST_ARGS[@]}"
