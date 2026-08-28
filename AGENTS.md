# AGENTS.md — VoxLogicA-2

Rules, not advice. Each one was paid for.

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

Size it, excerpt it. Never paste raw output into the conversation.

## Experiments

- Independent variants run **at once**; collect results at the end.
- Independent = separate out path, `work_root`, store db. Sharing one is a race,
  not an experiment.

## Performance

- `--profile` is deprecated and can be arbitrarily wrong
  (*1389 s of cumulative time reported inside a 52 s run*).
- Use `/usr/bin/time -v` `%CPU` plus the run's own saturation / cpu-per-wall.
- Reducer debugging only, if at all: flag **after** the filename (it eats it
  otherwise), 5–10 cases, with a timeout. No effect under `--no-engine`.
- A real find: `percentiles`' sort, not the scheduler — `HANDOVER.md` §0b/0c.

## Reporting

- Answer first. Answer only what was asked.
- Status = 3 lines. Result = table + 2 sentences. More than that: ask.
- One question at a time.
- Where things stand = goal / state / blocker / next.
- Flag hidden assumptions and open loops.
- Standard English terms, or defined in the same sentence — every time.
- "measured" vs "I think". Never let a guess arrive in the register of a fact.

## Result tables

Every row states **target · inputs · method**. No number appears without the
number it is compared to.
*Two rows differing in whether they used the ground truth must not look comparable.*

## Irreversible actions

- Ask first: deleting data, discarding a checkpoint, killing a long run,
  force-push, clearing a warmed store.
- A merely risky assumption: state it in one line, then proceed.
- When corrected, fix the thing — not the framing.
