#!/usr/bin/env bash
# One measured run of one program, with the two preconditions that make the
# number mean something.
#
#   1. THE MACHINE MUST BE QUIET, and quiet for a while. On a shared host the
#      same program has been measured at 55, 79 and 137 seconds while another
#      user's training came and went. So this waits for foreign CPU to stay
#      below a threshold for a hold period, and says how long it waited.
#   2. THE STORE MUST NOT BE ON tmpfs. /tmp on the reference host is a 31 GB
#      tmpfs; a store placed there consumes the very RAM the engine sizes its
#      budget from, which silently invalidated every performance number taken
#      over one whole day.
#
# Usage: run.sh <program.imgql> <output.tsv> [-- extra voxlogica flags...]
set -euo pipefail

PROGRAM=${1:?usage: run.sh <program.imgql> <output.tsv> [-- flags...]}
OUT=${2:?usage: run.sh <program.imgql> <output.tsv> [-- flags...]}
shift 2
[ "${1:-}" = "--" ] && shift || true

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
QUIET_PCT=${QUIET_PCT:-100}     # foreign CPU, in percent-of-one-core units
HOLD_S=${HOLD_S:-60}            # how long it must stay below that

foreign_cpu() { ps -eo pcpu,user --no-headers | awk -v me="$(id -un)" '$2 != me {s += $1} END {print int(s)}'; }

waited=0; held=0
while :; do
    c=$(foreign_cpu)
    if [ "${c:-99999}" -lt "$QUIET_PCT" ]; then held=$((held + 5)); else held=0; fi
    [ "$held" -ge "$HOLD_S" ] && break
    sleep 5; waited=$((waited + 5))
    if [ $((waited % 300)) -eq 0 ]; then
        echo "run.sh: still waiting for the machine (${c}% foreign, need <${QUIET_PCT}% for ${HOLD_S}s)" >&2
    fi
done

case "$(realpath "$OUT")" in
  /tmp/*|/dev/shm/*) echo "run.sh: refusing to write a measurement under $(dirname "$OUT") (tmpfs)" >&2; exit 2 ;;
esac
for i in "$@"; do
    case "$i" in
      /tmp/*|/dev/shm/*) echo "run.sh: refusing a store or path under tmpfs: $i" >&2; exit 2 ;;
    esac
done

mkdir -p "$(dirname "$OUT")"
echo "run.sh: machine quiet for ${HOLD_S}s after waiting ${waited}s; measuring" >&2
exec "$REPO/voxlogica" run --no-serve --measure "$OUT" "$@" "$PROGRAM"
