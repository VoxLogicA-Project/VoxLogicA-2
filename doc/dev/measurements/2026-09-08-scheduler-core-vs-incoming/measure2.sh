#!/usr/bin/env bash
# Second measurement of the same three configurations.
#
# The first one is void: the `core` worktree was a commit behind, at the
# withdrawn speculation policy, which fails deterministically with
# "'>' not supported between instances of 'Handle' and 'Handle'" and so
# ABORTED at 13 of 16 goals. Its 26 s was not a fast run, it was a short one.
# Every row here therefore records the goal count and the error code beside
# the time, and the load of the other users during the run, so a contaminated
# or truncated row can be thrown away instead of averaged.
set -u
WORK=/home/vincenzo/meas; OUT=$WORK/m2.tsv; LOG=$WORK/m2.log
PROG=/home/vincenzo/data/local/repos/VoxLogicA-2/doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql
VENV=/home/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python
: > $LOG
echo -e "rep\tvariant\titk\twall_s\tmean_cpu\tpeak_cpu\tothers\tgoals\terr\tdice" > $OUT

others_cpu() { ps -eo pcpu,user --no-headers | awk '$2 != "vincenzo" {s += $1} END {print int(s)}'; }

run_one() {   # variant repo itk rep
    local v=$1 repo=$2 itk=$3 rep=$4
    local db=$WORK/m2_${v}_${itk}_${rep}.db log=$WORK/m2_${v}_${itk}_${rep}.out
    rm -rf "$db" "$db-wal" "$db-shm" "${db}.files"
    echo "$(date +%H:%M:%S) $v itk=$itk rep=$rep (others $(others_cpu)%)" >> $LOG
    env PYTHONPATH=$repo/implementation/python \
        ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=$itk \
        $VENV -m voxlogica.main run --no-serve --threads 32 --error-details \
              --store-db "$db" "$PROG" > "$log" 2>&1 &
    local pid=$! prev=0 t=0
    local samples=$WORK/m2_${v}_${itk}_${rep}.cpu oth=$WORK/m2_${v}_${itk}_${rep}.oth
    : > "$samples"; : > "$oth"
    local start=$(date +%s%N)
    while kill -0 $pid 2>/dev/null; do
        local cur=$(awk '{print $14+$15}' /proc/$pid/stat 2>/dev/null || echo "$prev")
        if [ "$t" -gt 1 ]; then echo "$((cur - prev))" >> "$samples"; others_cpu >> "$oth"; fi
        prev=$cur; t=$((t+1)); sleep 1
    done
    wait $pid
    local wall=$(awk -v a=$start -v b=$(date +%s%N) 'BEGIN{printf "%.2f", (b-a)/1e9}')
    local st=$(awk '{s+=$1; n++; if($1>m) m=$1} END{printf "%.0f %.0f", s/n, m}' "$samples")
    printf "%s\t%s\t%s\t%s\t%s%%\t%s%%\t%s%%\t%s\t%s\t%s\n" \
        "$rep" "$v" "$itk" "$wall" \
        "$(echo $st|cut -d' ' -f1)" "$(echo $st|cut -d' ' -f2)" \
        "$(awk '{s+=$1;n++} END{printf "%.0f", s/n}' "$oth")" \
        "$(grep -acE '^[a-z_0-9]+=' "$log")" \
        "$(grep -aoE 'error\[[A-Z_]+\]' "$log" | head -1 | tr -d '\n' | sed 's/^$/-/;s/^/x/' | sed 's/^x$/-/;s/^x//')" \
        "$(grep -aoE '^dice_best_mean=[0-9.]*' "$log" | head -1 | cut -d= -f2)" >> $OUT
    rm -rf "$db" "$db-wal" "$db-shm" "${db}.files"
}

for rep in 1 2 3; do
    run_one base /tmp/vlx-main 24 $rep
    run_one base /tmp/vlx-main 1  $rep
    run_one core /tmp/vlx-loop 24 $rep
    run_one core /tmp/vlx-loop 1  $rep
done
echo "$(date +%H:%M:%S) done" >> $LOG
echo DONE >> $OUT
