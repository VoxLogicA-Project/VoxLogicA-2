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

## Profiling Commands

Profiling (`--profile wall`) adds ~2-3x overhead. When profiling is needed:
- Run on small cases (5–10 cases, not 369).
- Set explicit timeout and fail fast if it exceeds budget.
- Capture profile output separately (not just stdout tail).
