# Agent Guidelines for VoxLogicA-2

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
