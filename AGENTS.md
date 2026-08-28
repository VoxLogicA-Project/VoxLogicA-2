# AGENTS.md — VoxLogicA-2

Source of truth for how to work in this repository. `CLAUDE.md` only points here.

## Modes

### QUIET MODE

Use your intelligence to hyperoptimize saving AI tokens while you work.
Operate in quiet mode. Do not narrate your work or explain routine steps. Minimize token usage aggressively.

Only respond during the task if:
1. You are about to make a high-risk decision involving files, code, data loss, artifacts, or irreversible changes — ask for permission.
2. You make a moderately risky assumption — state it briefly.

Otherwise, stay silent until completion. At the end, provide only a 1–2 line summary, unless I ask for details.

Never paste raw shell command output directly into chat or source them into your context. Redirect all output to files, check file sizes first, and read only the relevant excerpts.

Say "Quiet mode on" to ack.

### ADHD MODE

Communicate for low executive-function load:
- Start with the answer/conclusion.
- Keep messages short, structured, and skimmable.
- Use bullets, headings, and concrete next actions.
- Ask at most one question at a time.
- Avoid long explanations unless I ask.
- Track context explicitly: goal, current state, blockers, next step.
- When I ramble or switch topics, summarize and bring me back.
- Prefer “do this now / next / later” over vague advice.
- Point out hidden assumptions and unfinished loops.
- Be direct, calm, non-judgmental, and practical.

Say "ADHD mode on" to ack. Do not ignore QUIET MODE. That's of paramount importance.

### PARALLEL MODE

When needing to explore different alternatives experimentally, run the experiments in parallel, if it's guaranteed they're isolated, and collect the results all together, do not run sequentially if possible. Say "parallel mode on" to ack. Do not ignore QUIET MODE. That's of paramount importance.

Isolated means separate output path, separate `work_root`, separate store db. Two
runs sharing one are not two experiments; they are one experiment with a race in it.

### FAST MODE

Do not spend wall-clock on waiting or on repeating yourself:
- Run the test suite when a phase is finished, not after every edit.
- Never wait with `sleep`. Background work announces itself; a long run has a progress file.
- Send independent commands in one round, not one at a time.
- Do not re-read a file you just wrote to check that it was written.

Say "Fast mode on" to ack. Do not ignore QUIET MODE. That's of paramount importance.

## Git across machines

| | |
|---|---|
| Always | edit local → commit → push → `git pull` on the remote |
| Never | `ssh`+edit, `scp`, `rsync`, `cat`-over-ssh a **tracked** file |
| Remote ahead | `git pull --rebase` before anything else |
| Remote dirty | stop and resolve — never route around it with a copy |

Untracked artifacts (caches, datasets, `.db`) are exempt.
*Cost so far: a `utils.imgql` lost with its host; a `run_iter.sh` rebase conflict.*

## Long runs (>30 s)

- Launch detached to a log: `./run_iter.sh <f>.imgql <out> <db>`.
- Hand the user a **bare** `tail -f <file>` — no `awk`, `grep`, or loops.
- Progress bars need `ptyrun.py`; watch raw tqdm via `tr '\r' '\n'`.
- Report interim status: nodes, cases, elapsed. Never block silently.

## Command output

```bash
cmd > /tmp/x.out 2>&1; echo EXIT=$?; wc -l /tmp/x.out
```

## Engine autonomy — no env-var tuning

- **The engine MUST work automatically.** Correct behaviour, above all staying
  within memory, is never conditional on an env var, a flag, or any outside knob.
- Never ship an env var as the *fix* for a defect. An env var is acceptable only
  as a diagnostic whose default is already right for every supported workload.
- A workload that OOMs, thrashes or deadlocks unless a var is set is an engine
  bug. Fix the engine's own policy. Launchers must not bound memory or
  concurrency on its behalf.

## Whole-workload runs — no sharding, no supervisors

- Every experiment, however large, runs as one plain `voxlogica run <f>.imgql`.
- Shards, chunks, batches across processes, crash-retry supervisors: FORBIDDEN.
  They hide engine defects instead of exposing them.
- A full run that OOMs, crashes, stalls or thrashes is an ENGINE BUG — and never
  the `.imgql`'s fault. Diagnose the engine; never route around it.
- `looping_experiment/run_sharded.py` and `_scratch/chunk_*.imgql` are
  DEPRECATED, kept as history.

## Performance

- `--profile` is deprecated and can be arbitrarily wrong
  (*1389 s of cumulative time reported inside a 52 s run*).
- Use `/usr/bin/time -v` `%CPU` plus the run's own saturation / cpu-per-wall.
- Reducer debugging only, if at all: flag **after** the filename (it eats it
  otherwise), 5–10 cases, with a timeout. No effect under `--no-engine`.
- A real find: `percentiles`' sort, not the scheduler — `HANDOVER.md` §0b/0c.
- Efficiency target is 100%. A shortfall is justified with measured memory
  bandwidth against the machine's measured ceiling, never asserted.

## Reporting

- Answer only what was asked. A status is 3 lines; a result is a table plus 2
  sentences. More than that: ask first.
- Standard English terms, or defined in the same sentence — every time.
- "measured" vs "I think". Never let a guess arrive in the register of a fact.
- Validate claims against repository state before reporting them.

## Result tables

Every row states **target · inputs · method**. No number appears without the
number it is compared to.
*Two rows differing in whether they used the ground truth must not look comparable.*

## Irreversible here

Ask first: deleting data, discarding a checkpoint, killing a long run,
force-push, clearing a warmed store.
When corrected, fix the thing — not the framing.

## Repository conventions

- **Canonical:** GitHub Issues for lifecycle (backlog, priority, status,
  closure); `doc/` for technical requirements and design contracts; `META/` for
  policy notes only.
- **Where things go:** code in `implementation/`, tests in `tests/`, design in
  `doc/`, process in `META/`. No new root-level files unless asked.
- **Issues:** start substantial work from one, reference `#<n>` in commits and
  PRs, close with `Fixes #<n>`. Behaviour changes update `doc/`, or state no
  requirements impact.
- **Running and testing:** no new virtualenvs; `./voxlogica` for runs,
  `./tests/run-tests.sh` for suites. New behaviour ships with a test or a stated
  reason for none.
- **Code:** Python 3.11+ syntax and typing; docstrings on public and non-obvious
  internals; deterministic coordination over timeouts; event- and future-driven
  over polling; locks only where correctness needs them.
- **Branches:** short-lived, small mergeable slices, rebased from `main` often.
- **Docs:** concise, updated next to the code they describe, no status narratives
  and no chat logs in policy files.
- **At session start:** `README.md`, `META/SWE_POLICY.md`, `META/GUIDE.md`.
