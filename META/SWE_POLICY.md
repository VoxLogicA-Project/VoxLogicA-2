# Software Engineering Policy

## Status
- Version: 2026-02-25
- Applies to: all code, tests, documentation, and release work in this repository
- Policy owner: repository maintainers

## 1. Governance Model
1. GitHub is the system of record for planning and delivery lifecycle.
2. Repository content is the system of record for technical implementation and behavior contracts.
3. In case of conflict:
- lifecycle/state conflicts are resolved in GitHub
- technical behavior conflicts are resolved in versioned repository docs and code

## 2. Work Item Lifecycle (GitHub Canonical)
1. Every feature, bug, rewrite slice, and significant refactor starts from a GitHub issue.
2. Issues must define:
- problem statement
- scope and non-goals
- acceptance criteria
- risk level
3. Pull requests must reference issue IDs.
4. Pull requests should use closing keywords when completion criteria are met (for example `Fixes #123`).

## 3. Requirements Management
1. Requirements are versioned in-repo under `doc/`, linked from the owning GitHub issue.
2. Module-level requirements belong in `doc/dev/` and must include:
- scope
- assumptions and constraints
- acceptance criteria
- non-goals
- backward compatibility expectations
3. Code docstrings/comments are implementation contracts, not lifecycle trackers.
4. Any behavior-changing PR must update the relevant requirement document or explicitly state no requirements impact.

## 4. Traceability Requirements
1. Each merged change must be traceable across:
- GitHub issue
- code changes
- tests
- requirement/design docs (if behavior changed)
2. PR descriptions must include:
- issue reference
- validation evidence
- rollback or mitigation notes for risky changes
3. If no test is added for a behavior change, PR must provide a rationale.

## 5. Branching and Integration
1. Default model is trunk-based development with short-lived branches.
2. Branches should be small, vertical slices, and integrated frequently.
3. Long-lived branches are exceptions and require explicit maintainer approval.
4. Large rewrites must be split into incremental PRs under a tracking epic.
5. Rebase or merge from `main` frequently to control divergence.

## 6. Code Quality Standards
1. Changes must preserve readability, maintainability, and debuggability.
2. Avoid hidden coupling and undocumented side effects.
3. Public APIs and critical internal contracts must be documented in docstrings or module docs.
4. Breaking changes must be explicitly documented in PR and release notes.

## 7. Testing Standards
1. Tests must be placed under `tests/`.
2. `pytest` is the canonical test runner (`./tests/run-tests.sh` is the repository entrypoint wrapper).
3. For behavior changes, tests should cover:
- nominal path
- edge conditions
- regression risk
4. High-risk changes should include targeted failure-mode validation.
5. Issue closure requires passing relevant tests and human approval.

## 8. Documentation Standards
1. Docs must be concise, accurate, and versioned with code.
2. Avoid duplicated status narratives across multiple files.
3. Prefer linking canonical sources over copying state.
4. When architecture decisions are significant, record them in `doc/dev/`.

## 9. Release and Change Control
1. Merge only when acceptance criteria and validation evidence are satisfied.
2. For high-impact changes, include:
- migration notes
- operational impact
- rollback plan
3. Keep release-facing documentation aligned with actual behavior.

## 10. Security and Reliability Baseline
1. Treat data loss, corruption, deadlock, and concurrency bugs as high severity by default.
2. Validate error handling, logging clarity, and recovery behavior in critical paths.
3. Do not merge changes that reduce observability of critical failures without replacement controls.

## 11. Policy Exceptions
1. Exceptions are allowed only when documented in the relevant GitHub issue/PR.
2. Exception records must include:
- reason
- scope
- risk assessment
- expiry or follow-up action
