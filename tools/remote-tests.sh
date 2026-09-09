#!/usr/bin/env bash
# Run the suite on fmt-5000 instead of the laptop: the laptop has 18 cores and
# gets hot, the reference host has 24 and is the machine every performance
# number is taken on anyway.
#
# Usage: remote-tests.sh [pytest args...]     (default: the whole suite)
set -euo pipefail
LOCAL=/Users/vincenzo/data/local/repos/vlx-loop
REMOTE=/tmp/vlx-loop
LOG=${TESTLOG:-/private/tmp/claude-501/remote-tests.log}

# Only the source tree, and only what pytest reads. rsync rather than git push,
# so an uncommitted work-in-progress can be tested before it is committed.
rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
      --exclude '__pycache__' --exclude '*.pyc' \
      "$LOCAL"/implementation "$LOCAL"/tests "$LOCAL"/tools "$LOCAL"/pytest.ini \
      "$LOCAL"/doc "$LOCAL"/synthetic_data \
      fmt-5000:"$REMOTE"/ >/dev/null

ssh fmt-5000 "cd $REMOTE && PYTHONPATH=$REMOTE/implementation/python \
  /home/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python -m pytest ${*:-tests -q}" \
  > "$LOG" 2>&1
rc=$?
tail -4 "$LOG"
exit $rc
