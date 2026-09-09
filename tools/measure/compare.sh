#!/usr/bin/env bash
# Compare configurations of the same program, INTERLEAVED.
#
# Repetitions are interleaved rather than blocked (A B C, A B C, A B C -- never
# A A A, B B B) because a machine drifts: another user arrives, the disk fills,
# thermals change. Blocked repetitions attribute that drift to whichever
# configuration was running at the time, and that is how a 26.3-second run of a
# BROKEN build was once reported as the fastest of three.
#
# Each configuration is a line in a config file: a label, a tab, then flags.
# Every run gets its own store, on real disk, deleted afterwards.
#
# Usage: compare.sh <program.imgql> <configs.tsv> <outdir> [reps]
set -euo pipefail

PROGRAM=${1:?usage: compare.sh <program.imgql> <configs.tsv> <outdir> [reps]}
CONFIGS=${2:?}
OUTDIR=${3:?}
REPS=${4:-3}

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
mkdir -p "$OUTDIR"
# The config file must not live where the report globs, or it is read as data.
[ "$(cd "$(dirname "$CONFIGS")" && pwd)" = "$(cd "$OUTDIR" && pwd)" ] && \
  echo "compare.sh: note -- the config file is inside the output directory" >&2

mapfile -t lines < <(grep -v '^\s*#' "$CONFIGS" | grep -v '^\s*$')
for rep in $(seq 1 "$REPS"); do
    for line in "${lines[@]}"; do
        label=${line%%$'\t'*}
        flags=${line#*$'\t'}
        out=$OUTDIR/${label}_rep${rep}.tsv
        db=$OUTDIR/${label}_rep${rep}.db
        echo "== $label rep $rep" >&2
        rm -rf "$db" "$db-wal" "$db-shm" "$db.files"
        # shellcheck disable=SC2086
        "$REPO/tools/measure/run.sh" "$PROGRAM" "$out" -- --store-db "$db" $flags \
            > "$OUTDIR/${label}_rep${rep}.out" 2>&1 || echo "  FAILED, see ${label}_rep${rep}.out" >&2
        rm -rf "$db" "$db-wal" "$db-shm" "$db.files"
    done
done

echo >&2
"$REPO/.venv/bin/python" "$REPO/tools/measure/report.py" "$OUTDIR"/*_rep*.tsv
