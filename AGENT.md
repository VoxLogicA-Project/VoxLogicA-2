# AGENT INSTRUCTIONS

This file defines repository-local operating rules for coding agents.
It must stay consistent with `META/SWE_POLICY.md` and `META/GUIDE.md`.

## 1. Source of Truth

1. GitHub Issues are canonical for lifecycle management:
- backlog
- priority
- ownership
- status
- closure

2. In-repo documentation under `doc/` is canonical for technical requirements and design contracts.

3. `META/` is for policy and supporting notes only.

## 2. Where Changes Belong

1. Implementation code goes in `implementation/`.
2. Tests go in `tests/`.
3. Requirements/design docs go in `doc/`.
4. Process and local notes go in `META/`.
5. Avoid creating new root-level files unless explicitly requested.

## 3. Issue and Requirement Workflow

1. Start substantial work from a GitHub issue.
2. Reference the issue in commits and PRs (`#<issue-number>`).
3. Use closing keywords in PRs when acceptance criteria are met (`Fixes #<issue-number>`).
4. For behavior changes, update relevant requirement/design docs in `doc/` or explicitly state no requirements impact.

## 4. Testing and Execution Workflow

1. Do not create virtual environments; use existing project tooling.
2. Prefer repository entrypoints:
- `./voxlogica` for user-facing runs
- `./tests/run-tests.sh` (or `python -m tests.run_tests` when needed by test infra) for test suites
3. Do not add tests outside `tests/`.
4. New behavior should include tests, or a documented reason for no test.

## 5. Engine Autonomy — No Environment-Variable Tuning

1. **The engine MUST work automatically.** Correct behavior — above all, staying
   within memory — is never conditional on the user setting an environment
   variable, a flag, or any other external knob.
2. Never propose, document, or ship an env var as the *fix* for an engine defect
   (e.g. `VOXLOGICA_PERSIST_MIN_MS`, `VOXLOGICA_MAX_LIVE_GB`, `VOXLOGICA_THREADS`).
   An env var is acceptable only as a diagnostic or a measure-it-yourself research
   knob whose default is already correct for every supported workload.
3. A workload that OOMs, thrashes, or deadlocks unless an env var is set is an
   engine bug. Fix the engine's own policy so the default path self-regulates.
4. The same applies to launcher scripts: `run_iter.sh` and friends must not have
   to bound concurrency, threads, or memory on the engine's behalf.

## 6. Whole-Workload Runs — No Sharding, No Supervisors

1. Every experiment, however large, MUST run as a single plain invocation:
   `voxlogica run <file>.imgql`. The engine carries it from start to end.
2. Splitting a workload into shards/chunks/batches across processes is
   FORBIDDEN, as are crash-retry supervisors and any launcher that caps
   concurrency, threads, or memory on the engine's behalf. These hide engine
   defects instead of exposing them and are a known drifting-agent failure mode.
3. A full run that OOMs, crashes, stalls, or thrashes is an ENGINE BUG. Diagnose
   and fix the engine; never route around it.
4. Existing sharding machinery (`looping_experiment/run_sharded.py` and the
   `_scratch/chunk_*.imgql` pattern) is DEPRECATED and kept only as history.
5. Efficiency target is 100%. Any shortfall MUST be justified with factual
   evidence — measured memory bandwidth against the machine's measured ceiling —
   never asserted.

## 7. Coding Standards

1. Use Python 3.11+ syntax and typing.
2. Use clear docstrings for public functions/classes and complex internal logic.
3. Prefer deterministic coordination over timeout-based control flow.
4. Use locking only when necessary and justified by correctness constraints.
5. Prefer event/future-driven coordination over polling where practical.

## 8. Branching and Change Size

1. Prefer short-lived branches and small, mergeable slices.
2. Avoid long-lived rewrite branches unless explicitly approved.
3. Rebase/merge from `main` frequently to limit divergence.

## 9. Documentation Hygiene

1. Keep docs concise and avoid duplicated status narratives.
2. Update docs close to code changes so contracts remain accurate.
3. Do not store temporary chat logs or ephemeral notes in policy files.

## 10. Startup and Verification Expectations

1. At session start, read:
- `README.md`
- `META/SWE_POLICY.md`
- `META/GUIDE.md`
2. Validate claims against repository state before reporting conclusions.
