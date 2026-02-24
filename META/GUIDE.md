# META Directory Guide

## Purpose
The `META` directory stores software engineering policies, working notes, and project process artifacts.

## Canonical Workflow
- **GitHub Issues are canonical** for backlog, status, priority, ownership, and closure.
- **Repository docs are canonical** for technical requirements and design decisions.

## How META Is Used
- **Policies:** Active policy lives in `META/SWE_POLICY.md`.
- **Tasks/notes:** `META/TASKS/` contains local implementation notes.
- **Issue artifacts (optional):** `META/ISSUES/` may contain supplemental local notes linked to GitHub issues, but is not the system of record.

## Archived Guidance
- Historical guidance that treated local `META/ISSUES/OPEN` and `META/ISSUES/CLOSED` as canonical tracking is archived.
- Existing local issue directories are retained for history/reference unless explicitly cleaned up.

## Maintenance Rules
- Keep this directory concise and non-duplicative.
- Prefer links to canonical docs/issues over copying status in multiple places.
- When in doubt, update GitHub issue state first, then update local notes if needed.

## Practical Rules
- Do not rely on `META/ISSUES` directory movement for official open/closed status.
- If creating a local issue note, include GitHub issue ID and URL in the note header.
- Store implementation scratch notes in `META/TASKS/` when they are not durable requirements.
