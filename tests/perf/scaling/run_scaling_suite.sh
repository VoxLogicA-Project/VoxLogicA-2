#!/bin/bash
# Full measurement protocol behind manuscripts/engine-scaling-2026-07.md.
# This is a cleaned-up, parameterized version of the ad hoc scripts actually
# run against fmt-5000 during that investigation -- same measurements, same
# order, same discriminators, generalized to any host/imgql instead of hardcoded
# paths. See doc/dev/scaling-test-design.md sec 5 for the protocol rules this
# encodes (idle gate, interleaving, min-of-N, genuinely serial baseline) and
# sec 0 for WHY they are non-negotiable: four contradictory conclusions were
# reached in one session by skipping them.
#
# Usage: ./run_scaling_suite.sh OUT_DIR [IMGQL] [P_CORES] [E_CORES]
#   OUT_DIR   where results land (created if missing)
#   IMGQL     workload (default: bench_scaling.imgql next to this script)
#   P_CORES   taskset range for the fast cores, e.g. "0-7"  (default: auto-detect)
#   E_CORES   taskset range for the slow cores, e.g. "8-23" (default: auto-detect)
#
# Stages (see header comments below for what each measures and why):
#   0. idle gate                      -- refuse to run on a loaded/contaminated host
#   1. wall-clock + saturation sweep  -- runs anywhere, no perf/taskset needed
#   2. P-core-only sweep (needs perf, taskset, and a detected P-core group)
#   3. E-core-only sweep (ditto, E-core group)
#   4. topdown + perf record          -- names the cause, not just its shape
#
# Stages 2-4 are skipped with a clear message if this host has no P/E split
# (non-hybrid CPU) or lacks `perf`/`taskset` -- stage 1 alone still answers
# "does the scheduler achieve what it's asked" via `saturation`.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:?usage: $0 OUT_DIR [IMGQL] [P_CORES] [E_CORES]}"
IMGQL="${2:-$SCRIPT_DIR/bench_scaling.imgql}"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHONPATH_VAL="$REPO_ROOT/implementation/python"
PY="$REPO_ROOT/.venv/bin/python"
BENCH_HASH='avg_oracle_best='  # bit-exactness marker; grep for the value your own run prints and compare

mkdir -p "$OUT_DIR"
cd "$(dirname "$IMGQL")" || exit 1
IMGQL_BASENAME="$(basename "$IMGQL")"

# ---------------------------------------------------------------------------
# Stage 0 -- idle gate. A busy or contaminated host produces unfalsifiable
# numbers (doc/dev/scaling-test-design.md sec 0: a GPU-resident LLM server and
# a mid-run laptop hibernation each independently produced a wrong conclusion
# in this exact study before this gate existed).
# ---------------------------------------------------------------------------
idle_gate() {
  local cores load
  cores=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
  load=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
  [ -z "$load" ] && load=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')
  if [ -n "$load" ] && awk -v l="$load" -v c="$cores" 'BEGIN{exit !(l > 0.3*c)}'; then
    echo "ABORT: load $load too high for $cores cores (want < $(awk -v c=$cores 'BEGIN{print 0.3*c}'))" >&2
    return 1
  fi
  if pgrep -af 'ollama|llama_cpp|lmstudio|text-generation' 2>/dev/null | grep -qv grep; then
    echo "ABORT: a local inference server is running -- it will contaminate every measurement" >&2
    return 1
  fi
  return 0
}
# Escape hatch for smoke-testing the harness itself against a trivial program
# (see README.md) on an otherwise-busy dev box. NEVER set this for a real
# measurement run -- it exists to test plumbing, not to skip the reason the
# gate exists (doc/dev/scaling-test-design.md sec 0).
if [ "${VOXLOGICA_SKIP_IDLE_GATE:-0}" != "1" ]; then
  idle_gate || exit 1
else
  echo "WARNING: idle gate skipped (VOXLOGICA_SKIP_IDLE_GATE=1) -- results are not measurement-grade" >&2
fi

# ---------------------------------------------------------------------------
# Detect P-core / E-core groups (Linux hybrid-CPU sysfs; empty on non-hybrid
# or non-Linux hosts, in which case stages 2-4 are skipped, not faked).
# ---------------------------------------------------------------------------
P_CORES="${3:-}"
E_CORES="${4:-}"
if [ -z "$P_CORES" ] && [ -r /sys/devices/cpu_core/cpus ]; then
  P_CORES=$(cat /sys/devices/cpu_core/cpus)
fi
if [ -z "$E_CORES" ] && [ -r /sys/devices/cpu_atom/cpus ]; then
  E_CORES=$(cat /sys/devices/cpu_atom/cpus)
fi
HAVE_PERF=0; command -v perf >/dev/null 2>&1 && command -v taskset >/dev/null 2>&1 && HAVE_PERF=1

# macOS ships BSD `time`, which has no -f/-o (GNU-only); `gtime` (coreutils via
# brew) is the portable equivalent where available. Without either, cpu/wall is
# skipped rather than silently producing empty files (which is what happened
# the first time this was tested on the Mac -- `-f`/`-o` were accepted and
# ignored, `time` wrote nothing, and every downstream read failed quietly).
GNU_TIME=""
if /usr/bin/time -f "%e" true >/dev/null 2>/tmp/.gnu_time_probe.$$; then
  GNU_TIME="/usr/bin/time -f"
elif command -v gtime >/dev/null 2>&1; then
  GNU_TIME="gtime -f"
fi
rm -f /tmp/.gnu_time_probe.$$
[ -z "$GNU_TIME" ] && echo "NOTE: no GNU-compatible 'time' found -- cpu/coresbusy columns will read 'n/a' (install GNU coreutils' gtime for full output)" >&2

run_one() {  # $1=label $2=taskset_range_or_empty $3=workers $4=out_prefix $5=perf_events_or_empty
  local label="$1" cores="$2" w="$3" prefix="$4" events="$5"
  local f="$OUT_DIR/${prefix}"
  # Build the invocation as an array (no eval): [time wrapper] [taskset] [perf] python ...
  local cmd=()
  if [ -n "$GNU_TIME" ]; then
    if [ "$GNU_TIME" = "gtime -f" ]; then cmd+=(gtime -f "%e %U %S" -o "${f}.time")
    else cmd+=(/usr/bin/time -f "%e %U %S" -o "${f}.time"); fi
  fi
  [ -n "$cores" ] && cmd+=(taskset -c "$cores")
  if [ -n "$events" ] && [ "$HAVE_PERF" = 1 ]; then cmd+=(perf stat -e "$events" -o "${f}.perf" --); fi
  cmd+=(env PYTHONPATH="$PYTHONPATH_VAL" ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1 "$PY"
        -m voxlogica.main run "$IMGQL_BASENAME" --no-cache --threads "$w"
        --store-db "${f}.sqlite")
  "${cmd[@]}" > "${f}.log" 2>&1
  local wall cpu ok
  wall=$(grep -a 'Execution time' "${f}.log" | tail -1 | grep -oE '[0-9]+\.[0-9]+')
  if [ -n "$GNU_TIME" ] && [ -f "${f}.time" ]; then
    read -r _e u s < "${f}.time" 2>/dev/null
    cpu=$(awk -v u="${u:-0}" -v s="${s:-0}" 'BEGIN{printf "%.1f", u+s}')
  else
    cpu="n/a"
  fi
  ok=$(grep -ac "$BENCH_HASH" "${f}.log")
  echo "$label workers=$w wall=${wall:-FAIL} cpu=$cpu bitmarker_present=$ok"
}

echo "=== Stage 1: wall-clock + saturation sweep (portable) ===" | tee -a "$OUT_DIR/summary.txt"
for w in 1 2 4 8; do
  run_one "sweep" "" "$w" "sweep_w$w" "" | tee -a "$OUT_DIR/summary.txt"
  grep -ao '"saturation": [0-9.]*\|"mean_concurrency": [0-9.]*' "$OUT_DIR/sweep_w$w.log" | tr '\n' ' '
  echo
done

if [ "$HAVE_PERF" = 1 ] && [ -n "$P_CORES" ]; then
  echo "=== Stage 2: P-core-only sweep ($P_CORES) ===" | tee -a "$OUT_DIR/summary.txt"
  n_p=$(( $(echo "$P_CORES" | tr ',' '\n' | awk -F- '{print $2-$1+1}' | paste -sd+ | bc 2>/dev/null || echo 0) ))
  for w in 1 2 4; do
    [ "$w" -le "${n_p:-8}" ] || continue
    run_one "pcore" "$P_CORES" "$w" "pcore_w$w" "cpu_core/instructions/,cpu_core/cycles/" \
      | tee -a "$OUT_DIR/summary.txt"
  done
else
  echo "Stage 2 SKIPPED: no perf/taskset, or no detected P-core group" | tee -a "$OUT_DIR/summary.txt"
fi

if [ "$HAVE_PERF" = 1 ] && [ -n "$E_CORES" ]; then
  echo "=== Stage 3: E-core-only sweep ($E_CORES) ===" | tee -a "$OUT_DIR/summary.txt"
  for w in 1 4; do
    run_one "ecore" "$E_CORES" "$w" "ecore_w$w" "cpu_atom/instructions/,cpu_atom/cycles/" \
      | tee -a "$OUT_DIR/summary.txt"
  done
else
  echo "Stage 3 SKIPPED: no perf/taskset, or no detected E-core group" | tee -a "$OUT_DIR/summary.txt"
fi

if [ "$HAVE_PERF" = 1 ] && [ -n "$P_CORES" ]; then
  echo "=== Stage 4: topdown attribution + perf record symbols (P-cores) ===" | tee -a "$OUT_DIR/summary.txt"
  f="$OUT_DIR/topdown"
  taskset -c "$P_CORES" perf stat -M TopdownL1 -e cpu_core/instructions/,cpu_core/cycles/ \
    -o "${f}.perf" -- env PYTHONPATH="$PYTHONPATH_VAL" "$PY" -m voxlogica.main run \
    "$IMGQL_BASENAME" --no-cache --threads 4 --store-db "${f}.sqlite" > "${f}.log" 2>&1
  grep -aE "tma_|instructions|cycles" "${f}.perf" | tee -a "$OUT_DIR/summary.txt"

  rf="$OUT_DIR/record"
  taskset -c "$P_CORES" perf record -F 199 -g --call-graph=fp -o "${rf}.data" -- \
    env PYTHONPATH="$PYTHONPATH_VAL" "$PY" -m voxlogica.main run "$IMGQL_BASENAME" \
    --no-cache --threads 4 --store-db "${rf}.sqlite" > "${rf}.log" 2>&1
  perf report -i "${rf}.data" --no-children --percent-limit 0.5 --stdio 2>/dev/null \
    | grep -aE "^ *[0-9]+\.[0-9]+%" | tee -a "$OUT_DIR/summary.txt"
else
  echo "Stage 4 SKIPPED: no perf/taskset, or no detected P-core group" | tee -a "$OUT_DIR/summary.txt"
fi

echo "SUITE_DONE" | tee -a "$OUT_DIR/summary.txt"
echo "Results in $OUT_DIR/summary.txt (and per-stage .log/.perf files)"
