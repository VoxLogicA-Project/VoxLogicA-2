#!/bin/bash
# Recompile the hashed, cross-platform locks from the two requirements files.
#
# Run this after editing implementation/python/requirements*.txt, and commit
# the .lock files with the change. The locks are what bootstrap.py installs;
# an edited .txt with a stale lock changes nothing.
#
# --universal so one lock serves every platform a reviewer might use: the
# resolution carries environment markers rather than assuming this machine.
# --generate-hashes so a package that is re-uploaded under the same version
# fails the install instead of silently substituting itself.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
REQ="$ROOT/implementation/python"

UV="$(command -v uv || echo "$ROOT/.cache/uv/bin/uv")"
[ -x "$UV" ] || { echo "uv not found; run ./voxlogica once to fetch it" >&2; exit 1; }

# The interpreter the engine actually requires, not whatever is on PATH.
PY_VERSION="$(cut -d. -f1,2 < "$ROOT/.python-version")"

echo "uv:     $("$UV" --version)"
echo "python: $PY_VERSION (from .python-version)"

"$UV" pip compile --universal --generate-hashes --python-version "$PY_VERSION" \
  "$REQ/requirements.txt" -o "$REQ/requirements.lock"

# Compiled from BOTH files, so it is a superset: bootstrap installs this one
# alone when tests are included.
"$UV" pip compile --universal --generate-hashes --python-version "$PY_VERSION" \
  "$REQ/requirements.txt" "$REQ/requirements-test.txt" -o "$REQ/requirements-test.lock"

for f in requirements.lock requirements-test.lock; do
  printf '%-28s %4s packages\n' "$f" "$(grep -cE '^[a-zA-Z].*==' "$REQ/$f")"
done
