#!/usr/bin/env bash
# Is the low-CPU tail the export phase, whose width is `outlier_count`?
# Prediction: the tail sat at 640-1150% with 10 outliers, i.e. ~10 independent
# chains. With 20 it should roughly double; with 2 it should roughly halve.
set -u
W=/home/vincenzo/meas; R=/tmp/vlx-loop
V=/home/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python
SRC=/home/vincenzo/data/local/repos/VoxLogicA-2/doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql
for n in 2 20; do
    p=$W/tail_$n.imgql
    sed "s/^outlier_count = 10/outlier_count = $n/" $SRC > $p
    db=$W/tail_$n.db; rm -rf $db $db-wal $db-shm $db.files
    env PYTHONPATH=$R/implementation/python ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=24 \
        $V -m voxlogica.main run --no-serve --threads 32 --error-details \
           --store-db $db $p > $W/tail_$n.out 2>&1 &
    pid=$!; prev=0; t=0; : > $W/tail_$n.cpu
    while kill -0 $pid 2>/dev/null; do
        cur=$(awk '{print $14+$15}' /proc/$pid/stat 2>/dev/null || echo $prev)
        [ $t -gt 1 ] && echo $((cur - prev)) >> $W/tail_$n.cpu
        prev=$cur; t=$((t+1)); sleep 1
    done
    wait $pid
    rm -rf $db $db-wal $db-shm $db.files
    echo "n=$n goals=$(grep -acE '^[a-z_0-9]+=' $W/tail_$n.out)" >> $W/tail.log
done
echo DONE >> $W/tail.log
