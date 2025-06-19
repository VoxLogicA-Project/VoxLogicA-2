# Software Engineering Policy: Feature Development Workflow

## Policy Statement

When developing a new feature in this repository, the following workflow is mandatory:


2. **Document the Issue in META**

   - Create a corresponding directory in META/ISSUES/OPEN/ (for open issues) or META/ISSUES/CLOSED/ (for completed issues) with a meaningful, descriptive name using yyyy-mm-dd-kebab-case (e.g., `2025-18-06-json-export-feature`)
   - It is mandatory to read the system date using a command and not use random dates instead.
   - Include a README.md file within such directory that describes the issue, its status, and related information.
   - For bugs or specific technical issues, include any reproduction steps or test files in this directory.

   **Issue Naming Convention:**

   - **Descriptive**: Clearly indicate what the issue is about
   - **Concise**: Keep names reasonably short (2-5 words)
   - **Kebab-case**: Use lowercase letters with hyphens as separators
   - **Action-oriented**: When possible, describe what was done (e.g., `fix-memory-leak` rather than `memory-problem`)

   Examples of good issue names:

   - `json-export-feature`
   - `tests-and-cli-output-fix`
   - `api-authentication-bug`
   - `performance-optimization-parser`
   - `docker-deployment-setup`

## History and Traceability

 - When the issue progresses, add comments in the README.md progressively, DO NOT DELETE OR MODIFY PAST COMMENTS OR CONTENT.

## Issue Completion Checklist (Best-Standards Policy)

When an issue is marked complete, the following steps are MANDATORY:

1. **CHECK WITH THE USER:**

   - should the issue be closed?

1. **META Update:**

   - Move the issue directory from META/ISSUES/OPEN/[issue-name] to META/ISSUES/CLOSED/[issue-name].
   - Update the README.md in the CLOSED directory to mark the issue as complete.
   - Ensure the main branch contains the authoritative, up-to-date information.
and remotely.

2. **Documentation:**

   - Ensure all documentation (README, design docs, CLI/API docs) is up to date and accurately reflects the completed work.

3. **Testing:**

   - Confirm all relevant tests pass and that the test suite covers the new/changed features.
   - Ensure new tests are present if required.
   - Use the testing infrastructure documented in the tests directory in the root of the repository.
   - When possible, each issue should be linked to one or more tests in the test infrastructure.
   - The issue is to be closed (with human permission) when the corresponding test(s) pass, providing automated traceability between issues and tests.
   - Not all issues are directly testable, but this linkage is required whenever feasible (e.g., for bug fixes, features, or regressions).
