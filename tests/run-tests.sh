#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."

if [ "${VOXLOGICA_SKIP_TEST_DEPS_INSTALL:-0}" != "1" ]; then
  python3 "$PROJECT_DIR/bootstrap.py" --with-test
fi

if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  VENV_PY="$PROJECT_DIR/.venv/bin/python"
elif [ -x "$PROJECT_DIR/.venv/Scripts/python.exe" ]; then
  VENV_PY="$PROJECT_DIR/.venv/Scripts/python.exe"
else
  echo "Virtualenv python not found under $PROJECT_DIR/.venv" >&2
  exit 1
fi

cd "$PROJECT_DIR"

# Best-effort fetch of VoxLogicA-1 binary used by cross-version parity/perf tests.
if [ "${VOXLOGICA_FETCH_VOX1:-1}" = "1" ]; then
  "$VENV_PY" tests/fetch_vox1_binary.py --quiet >/dev/null 2>&1 || true
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

"$VENV_PY" -m pytest "${PYTEST_ARGS[@]}"
