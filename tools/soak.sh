#!/bin/bash
# The stability matrix, and the number it exists to produce.
#
# WHY. Eleven scheduler defects were fixed on 2026-09-10 and the twelfth
# failure followed the same day. "It seems stable now" is not a claim a paper
# can carry, so this runs the matrix until it has either N consecutive clean
# runs or a counterexample, and writes one line per run to a TSV that is the
# evidence.
#
# THE ACCEPTANCE CRITERION, fixed here so it cannot drift: 20 consecutive
# complete runs, zero failures, `--verify` on, every report showing
# complete=true. Anything less is reported as the number it is.
#
# Each run is INDEPENDENT: its own store, its own report, its own log. A run
# that fails does not stop the matrix -- the point is the rate, not the first
# failure -- but it is recorded with its details id so the cone walk survives.
#
#   usage: soak.sh [rounds]        (default: keep going until stopped)
set -u

R=${VLX_ROOT:-/tmp/vlx-loop}
V=/home/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python
L=/home/vincenzo/data/local/repos/VoxLogicA-2/looping_experiment
OUT=${VLX_SOAK_OUT:-/tmp/soak}
ROUNDS=${1:-0}
mkdir -p "$OUT"
LEDGER=$OUT/ledger.tsv
[ -f "$LEDGER" ] || printf 'started\tprogram\tcache\tthreads\twall_s\texit\tgoals\tcomplete\tviolations\tdetails\tlog\n' > "$LEDGER"

# The programs, cheapest first: a failure on a short one is worth ten times a
# failure on a long one, because it can be reproduced.
PROGRAMS=(
  "$L/_scratch/soak_loops.imgql"
  "$L/_scratch/soak_readback.imgql"
  "/home/vincenzo/data/local/repos/VoxLogicA-2/doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql"
)
# The cache configurations. Every one of them has hosted a distinct defect:
# --no-cache found the store-not-load-bearing bug, --sparse-cache the spill
# queue, a warm store the unhonourable loop row.
CACHES=("--no-cache" "--sparse-cache" "--no-write-cache" "warm")
THREADS=(8 24 32)

# Two small programs whose shape is the one that keeps breaking: a loop over a
# COMPUTED bound, and a read back into the sequence it built.
mkdir -p "$L/_scratch"
cat > "${PROGRAMS[0]}" <<'IMGQL'
// A loop whose bound is a computed value, so the plan cannot unroll it
// statically and the engine must expand it at runtime. Every scheduler defect
// of 2026-09-10 was in that path.
import "test"
let n = 32
print "total"  fold + (for i in range(0, n) do spin(i, 60))
print "nested" fold + (for i in range(0, 4) do fold + (for j in range(0, 6) do spin(i + j, 60)))
IMGQL
cat > "${PROGRAMS[1]}" <<'IMGQL'
// Reading back into a lazily built sequence at a computed position: the shape
// of every goal that vanished when the store was not load-bearing.
import "test"
let s = for i in range(0, 24) do spin(i, 60)
print "aggregate" fold + s
print "readback"  index(s, argmax(s))
print "second"    index(s, argmax(for i in range(0, 24) do spin(i, 60)))
IMGQL

round=0
while : ; do
  round=$((round + 1))
  [ "$ROUNDS" -gt 0 ] && [ "$round" -gt "$ROUNDS" ] && break
  for prog in "${PROGRAMS[@]}"; do
    [ -f "$prog" ] || continue
    for cache in "${CACHES[@]}"; do
      for t in "${THREADS[@]}"; do
        stamp=$(date +%Y%m%d-%H%M%S)
        tag="$stamp-$(basename "$prog" .imgql)-${cache#--}-$t"
        log=$OUT/$tag.log
        rep=$OUT/$tag.json
        store=$OUT/store-$tag.db
        if [ "$cache" = "warm" ]; then
          # A warm store means the SAME store twice: the second run is the one
          # that exercises the read path, which is where the poisoned loop row
          # lived.
          flags="--sparse-cache --store-db $store"
          env PYTHONPATH=$R/implementation/python timeout 3600 $V -m voxlogica.main run \
            --no-serve --threads "$t" $flags --verify --error-details "$prog" \
            > "$log.warmup" 2>&1
        else
          flags="$cache --store-db $store"
        fi
        t0=$(date +%s)
        env PYTHONPATH=$R/implementation/python timeout 3600 $V -m voxlogica.main run \
          --no-serve --threads "$t" $flags --verify --error-details \
          --measure "$rep" "$prog" > "$log" 2>&1
        code=$?
        t1=$(date +%s)
        goals=$($V - "$rep" <<'PY' 2>/dev/null || echo "?"
import json, sys
try:
    r = json.load(open(sys.argv[1]))
    o = r.get("outcome", {})
    print(f'{o.get("goals_resolved")}/{o.get("goals_total")}')
except Exception:
    print("?")
PY
)
        complete=$($V - "$rep" <<'PY' 2>/dev/null || echo "?"
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("outcome", {}).get("complete"))
except Exception:
    print("?")
PY
)
        # NO `|| echo 0` HERE. `grep -c` exits 1 when it counts zero, so the
        # fallback fired ON TOP of grep's own "0" and put a newline in the
        # middle of the row -- every ledger line was split in two and the
        # reporter died on a missing column.
        viol=$(grep -c '^\[verify' "$log" 2>/dev/null | head -1)
        viol=${viol:-0}
        details=$(grep -o 'VLX-[0-9A-F]\{8\}' "$log" 2>/dev/null | head -1)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "$stamp" "$(basename "$prog")" "$cache" "$t" "$((t1 - t0))" \
          "$code" "$goals" "$complete" "$viol" "${details:--}" "$tag.log" \
          >> "$LEDGER"
        # The store is the biggest thing here and a failed run's store is
        # debris; keep only the failures' logs and reports.
        rm -rf "$store" "$store.files" "$store-wal" "$store-shm"
        if [ "$code" = "0" ] && [ "$complete" = "True" ] && [ "$viol" = "0" ]; then
          rm -f "$log" "$log.warmup" "$rep"
        fi
      done
    done
  done
done
