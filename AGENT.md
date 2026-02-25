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

## 5. Coding Standards

1. Use Python 3.11+ syntax and typing.
2. Use clear docstrings for public functions/classes and complex internal logic.
3. Prefer deterministic coordination over timeout-based control flow.
4. Use locking only when necessary and justified by correctness constraints.
5. Prefer event/future-driven coordination over polling where practical.

## 6. Branching and Change Size

1. Prefer short-lived branches and small, mergeable slices.
2. Avoid long-lived rewrite branches unless explicitly approved.
3. Rebase/merge from `main` frequently to limit divergence.

## 7. Documentation Hygiene

1. Keep docs concise and avoid duplicated status narratives.
2. Update docs close to code changes so contracts remain accurate.
3. Do not store temporary chat logs or ephemeral notes in policy files.

## 8. Startup and Verification Expectations

1. At session start, read:
- `README.md`
- `META/SWE_POLICY.md`
- `META/GUIDE.md`
2. Validate claims against repository state before reporting conclusions.
