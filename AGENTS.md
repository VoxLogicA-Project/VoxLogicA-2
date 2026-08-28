# Agent Guidelines for VoxLogicA-2

## Multi-Machine Work: Local First, Always

**CRITICAL:** When work spans more than one machine (e.g. a laptop and a compute
host), ALL editing happens on the LOCAL machine. Never edit files directly on the
remote.

The loop is always:

1. Edit locally.
2. Commit locally.
3. Push.
4. `git pull` on the remote, then run there.

**Never** `ssh remote` and edit a file, patch it with a script, or `scp` a
modified file onto it as a substitute for committing. A dropped connection, a
reboot, or simply forgetting leaves that work stranded on a host nobody looks at
— and it is invisible to `git status` locally, so it is lost silently rather than
loudly. Two failures of this kind have already happened here: a `utils.imgql`
that existed only on fmt-5000 and was lost when the host went down (its header
still records the reconstruction), and a `run_iter.sh` edited on both sides that
produced a rebase conflict the moment the two were reconciled.

Corollary: if the remote already has commits the local does not, `git pull
--rebase` BEFORE doing anything else. Diverging histories on a machine you only
reach over ssh are far more expensive to untangle than to prevent.

## Long-Running Commands (>30 seconds)

**CRITICAL:** Any command expected to run longer than 30 seconds MUST:

1. **Report a real-time watch command** to the user immediately after launching.
   - Format: a `tail -f` or `ssh ... tail -f` command that streams progress live.
   - Must be a self-contained command the user can copy-paste and run.

2. **Be designed for live observation:**
   - Output to a log file (e.g., `.out.raw` with PTY rendering for tqdm progress bars).
   - Use `ptyrun.py` wrappers if progress bars are involved.
   - Ensure tqdm/progress output renders line-by-line in the watch command's stream (convert `\r` to `\n`).

3. **Report interim status periodically** (while monitoring waits):
   - Node count, case count, elapsed time, cache size (if relevant).
   - Use `--progress` subcommand pattern (see `run_iter.sh`) for human-readable snapshots.

4. **Never block silently** — the user has no visibility into a backgrounded process unless told how to watch it.

Example pattern (from `run_iter.sh`):
```bash
# Launch with progress file
./run_iter.sh brats017_full.imgql _scratch/brats017.out _scratch/cache.db

# User watches with:
tail -f _scratch/brats017.out.progress   # human-readable snapshots every 20s
# or for raw tqdm:
tr '\r' '\n' < _scratch/brats017.out.raw | grep -a 'nodes:' | tail -1
```

## Syncing code between machines (Mac ↔ fmt-5000, or any two clones)

**CRITICAL: manual sync of code in a git repo is FORBIDDEN.** Never `scp`,
`rsync`, `cat`-over-ssh, or otherwise hand-copy a tracked file to update a
remote checkout — that's a silent, unreviewable, un-diffable edit that
bypasses history and will drift the two checkouts out of sync in ways
`git status` on either side won't reveal.

Always: commit locally → `git push` → on the remote, `git pull` (or `git
fetch` + `git checkout`/`git merge` if the branch diverged). If the remote
checkout has uncommitted local changes blocking the pull, stop and resolve
that (stash, commit, or ask the user) rather than routing around it with a
file copy.

This applies to source files, `.imgql` files, and any other tracked content
— not just Python. Untracked/generated artifacts (caches, datasets, `.db`
files) are not code and are not covered by this rule.

## Where the time went

**Do not answer a performance question with `--profile`.** It is deprecated and
the CLI says so on every use. cProfile has one global call stack and no
representation for this engine's concurrent workers, so its numbers can be
arbitrarily wrong -- measured: 1389 s of cumulative time reported inside a 52 s
run. A profile that confident and that wrong is worse than no profile.

Use instead:

```bash
/usr/bin/time -v ./voxlogica run PROGRAM.imgql        # %CPU is the real signal
```

together with the saturation and cpu-per-wall figures the run prints itself.
Those come from the engine and do count every worker.

`--profile` survives for one narrow job: single-threaded debugging of the
reducer. If you use it for that, it must come AFTER the filename --
`--profile` takes an optional PATH, so written before it, argparse consumes the
filename as its value:

```bash
./voxlogica run PROGRAM.imgql --profile                 # to stderr
./voxlogica run PROGRAM.imgql --profile=/tmp/out.pstats # then: snakeviz /tmp/out.pstats
```

It has no effect with `--no-engine`, and it costs 2-3x. Run it on 5-10 cases,
never on 369, and give it a timeout.

For a real bottleneck this once found -- `percentiles`' sort dominating a BraTS
case, not the scheduler -- see `HANDOVER.md` sections 0b and 0c.

## Command output belongs in a file, not in the conversation

Runs here produce logs measured in megabytes: a 369-case sweep, an nnU-Net
training, a pytest suite over a thousand tests. Piping any of that straight into
the conversation buries the finding under the transcript and costs context that
is then not available for the actual work.

Redirect it, check the size, and read only the part that answers the question:

```bash
<command> > /tmp/thing.out 2>&1; echo "EXIT=$?"; wc -l /tmp/thing.out
grep -E "FAILED|Error" /tmp/thing.out | head
```

The same applies to reporting: quote the lines that carry the result, not the
run. For a long run the progress file is the interface -- see "Long-Running
Commands" above, and watch it with a bare `tail -f`.

## Run independent experiments at the same time

When a question needs several variants tried, and the variants cannot interfere
-- separate output paths, separate work roots, separate store databases -- launch
them together and collect the results at the end. Running them one after another
turns an afternoon into a week, and this repository's experiments are long
enough that the difference decides what gets asked at all.

They must genuinely be isolated. Two nnU-Net trainings sharing a `work_root`, or
two runs sharing a store database, are not independent experiments; they are one
experiment with a race in it.

## How to communicate here

Sessions here run for days, a single run costs ten hours, and most of the
numbers look alike. These are the habits that keep that legible. They are not
one person's taste: each one is load-bearing at this scale, and the last section
lists what happened when they were not followed.

**Lead with the answer.** The conclusion first, the reasoning after, and only if
it is wanted. A report that builds to its finding makes the reader hold the whole
thing in their head to find out whether they needed it.

**Quiet by default.** Do not narrate routine steps or explain what a standard
command does. Work, then report. A status is three lines; a result is a table
plus two sentences.

**One question at a time.** Two questions in one message get one answer, and it
is usually unclear which.

**Say where things stand in four parts**: the goal, the current state, what is
blocking, what happens next. Especially after a long tool sequence, and
especially when handing back a thread that was interrupted.

**Name the hidden assumption and the loop left open.** Half the errors in this
repository's history were not wrong answers but unexamined premises -- a grid
assumed wide enough, a metric assumed comparable, a fix assumed verified. If
something is resting on an assumption, say which. If something was started and
not finished, say so before it is forgotten.

### Before doing something that cannot be undone

**Ask first.** Deleting or overwriting data, discarding a checkpoint, killing a
run that has been going for hours, force-pushing, clearing a store that took a
day to warm. The cost of asking is one message; the cost of not asking has
already been paid here more than once.

**A moderately risky assumption is stated in one line, then acted on.** Do not
stop and do not bury it -- say which way you went and why, and keep going.

### Mistakes actually made

Collected from corrections received while working on the BraTS experiment. Each
line is a mistake that was actually made, repeatedly.

**Answer only what was asked.** A question with two clauses gets two clauses. Do
not append status, context, or the next step unless asked. Extra material is
noise, and it buries the answer.

**Do not invent terminology.** Words like "target", "blind", "search",
"agreement", "grid", "box", "branch", "band" were used as if they were
established names. They were not. Either use a standard English term, or define
it in the same sentence, every time — a definition given three messages ago does
not count.

**Never show a number without the number it should be compared to.** A Dice of
0.87 means nothing on its own. If the headline is 0.9014 over 369 cases and the
figure being reported is one formula over 30 cases, say so in the same table row.
This caused "why don't I see 0.9 anywhere" twice.

**Every result table row states: target, inputs, method.** What the Dice is
measured against; what information was available to produce it, and in
particular whether the ground truth was among them; and how it was produced. Two
rows that differ in whether they used the truth must not look comparable.

**Separate measured from inferred, in the wording.** "The reclaim path only
evicts durable values" and "the grid shares its prefixes, so each point is cheap"
were both stated as findings and both were wrong — they were guesses that had
never been run. Say "measured" or say "I think"; do not let a mechanism arrive in
the register of a fact.

**If an explanation needs more room than that, ask whether it is wanted before
writing it.**

**When corrected, fix the thing — not the framing.** A workaround that makes an
error disappear is not a fix, and reporting it as one wastes the reader's trust
along with their time.
