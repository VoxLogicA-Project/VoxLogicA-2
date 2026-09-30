#!/bin/bash
# Is a resumed run slower than the same run left alone? (handover section 37s)
#
# 37s compared a cold run (540 node/s, first 35 min of the program) against a
# resume off a 1.1 TB store (431 node/s, a LATER part of the program, another
# cap). Two differences at once -- resuming, and which part of the program was
# running -- so the 20% was never shown to belong to the resume.
#
# Two arms, same code, same program, same cap, fresh store each, one after the
# other on the same machine. Both are compared over the SAME stretch of work:
# the second 35 minutes.
#
#   cont    cold, 70 minutes without interruption
#   kill    cold 35 minutes, kill -9, then resumed 35 minutes on the same store
#
# If kill's second half is slower than cont's, the resume itself costs. If not,
# the 37s gap belongs to the part of the program, or to the size of the store.
#
# The cap is 1000 GB so that neither arm evicts (~170 GB per million
# completions): eviction would be a third difference.
#
# usage: resume_ab.sh <engine checkout> <out dir>
set -u
R=$1; OUT=$2
V=/home/vincenzo/data/local/repos/VoxLogicA-2/.venv/bin/python
L=/home/vincenzo/data/local/repos/VoxLogicA-2/looping_experiment
PROG=$L/brats027_oracle60.imgql
HALF=2100                 # 35 min per half
REPORT_BEFORE=120         # ask for the report 2 min before a half ends
mkdir -p $OUT; cd $L

launch() {   # launch <tag> <store>  -> sets PID
  env PYTHONPATH=$R/implementation/python PTYRUN_LOG=$OUT/$1.log PTYRUN_COLS=180 \
    $V ptyrun.py $V -u -m voxlogica.main run --no-serve --threads 32 \
    --store-db $2 --cache-max-gb 1000 \
    --error-details --verify=report \
    --measure $OUT/$1.report.json --measure-series \
    --control $OUT/$1.ctl $PROG &
  PID=$!
}
report() { $V $R/tools/measure/ctl.py $OUT/$1.ctl report $OUT/$1.live.json >/dev/null 2>&1; }
killall_engine() {
  kill -$1 $PID 2>/dev/null
  for p in $(ps -eo pid,args | grep "[g]il=0 -m voxlogica.main" | awk '{print $1}'); do kill -$1 $p 2>/dev/null; done
}
wipe() { rm -rf $1 $1-wal $1-shm $1.files; }

echo "cont start $(date -Is)"
wipe $OUT/cont.db
launch cont $OUT/cont.db
sleep $((2*HALF - REPORT_BEFORE)); report cont; sleep $REPORT_BEFORE
killall_engine TERM; sleep 20; killall_engine 9; sleep 5
du -sh $OUT/cont.db.files 2>/dev/null; wipe $OUT/cont.db
echo "cont done $(date -Is)"

echo "kill start $(date -Is)"
wipe $OUT/kill.db
launch kill1 $OUT/kill.db
sleep $((HALF - REPORT_BEFORE)); report kill1; sleep $REPORT_BEFORE
killall_engine 9; sleep 5
echo "kill1 killed $(date -Is)"
launch kill2 $OUT/kill.db
sleep $((HALF - REPORT_BEFORE)); report kill2; sleep $REPORT_BEFORE
killall_engine TERM; sleep 20; killall_engine 9; sleep 5
du -sh $OUT/kill.db.files 2>/dev/null
echo "kill done $(date -Is)   (store kept: $OUT/kill.db)"
