# Software Engineering Policy: Feature Development Workflow

## Policy Statement

When developing a new feature in this repository, the following workflow is mandatory:

0. **Pre-Branching Cleanup (CRITICAL AND MANDATORY)**

   - Before creating any branch, the working directory MUST be clean: there must be no active changes (no uncommitted or unstaged files).
   - Always switch to the main branch, ensure it is up to date and clean, and only then proceed to create a new branch.
   - Failure to follow this rule can lead to changes being carried over to new branches unintentionally, causing confusion and potential merge conflicts.
   - This step is NOT optional and MUST be performed before creating any new feature branch.

1. **Create a GitHub Issue**

   - Open a new issue in the repository describing the feature, bug, or task to be addressed.
   - The issue should be clear, concise, and provide sufficient context.

2. **Document the Issue in META**

   - Create a corresponding directory in META/ISSUES/OPEN named ISSUE_X where X is the GitHub issue number for open issues. When an issue is closed, move its directory to META/ISSUES/CLOSED/ISSUE_X.
   - Include a README.md file within this directory that describes the issue, its status, and related information.
   - For bugs or specific technical issues, include any reproduction steps or test files in this directory.

3. **Create a Feature Branch**

   - Create a new branch from the default branch (usually `main` or `master`).
   - The branch name must be clear, unambiguous, and related to the issue.
   - **Best Practice:** Include the GitHub issue number in the branch name for traceability. For example: `feature/123-add-user-authentication` where `123` is the issue number.
   - Add a link to the feature branch in the GitHub issue.

4. **Switch to the Feature Branch**

   - Ensure all development for the feature (including design documents and implementation) is performed on this branch.

5. **Follow Best Practices**
   - Keep the branch focused on the issue described in the GitHub issue.
   - Reference the issue in commit messages and pull requests.
   - Regularly push changes to the remote branch.
   - Open a pull request referencing the issue when the feature is ready for review.

- All file and directory renames for versioned files (i.e., tracked by git) must be performed using git commands (e.g., 'git mv'), not by using 'mv' or other filesystem-only renaming methods. This ensures proper tracking of file history and avoids confusion in version control.

## Rationale

This policy ensures traceability, clarity, and alignment with industry best practices for collaborative software development. Each issue has a corresponding directory in META/ISSUES that contains all relevant information and documentation, ensuring seamless navigation and context for all contributors.

## Issue Completion Checklist (Best-Standards Policy)

When an issue is marked complete, the following steps are MANDATORY:

1. **META Update:**

   - Update the README.md in the corresponding META/ISSUES/ISSUE_X directory to mark the issue as complete.
   - Update WORKING_MEMORY.md to reflect the new status and remove or update any "ongoing" status for the issue.
   - Ensure the main branch contains the authoritative, up-to-date information.

2. **GitHub Issue and Branch Workflow:**

   - Update the GitHub issue to reflect completion.
   - Merge the feature branch into main **locally** (no pull request is required in this project; merges are performed directly on the developer's machine for traceability and speed).
   - After merging, update the META files with the merge commit SHA for traceability, and delete the feature branch locally and remotely.

3. **Documentation:**

   - Ensure all documentation (README, design docs, CLI/API docs) is up to date and accurately reflects the completed work.

4. **Testing:**

   - Confirm all relevant tests pass and that the test suite covers the new/changed features.
   - Ensure new tests are present if required.

5. **Traceability:**
   - Cross-reference all changes, issues, and branches for traceability (issue links in README files, branch names referencing issues, etc.).

## Task Completion and Branch Deletion Policy

- An issue can be marked as done by referencing the commit hash (SHA) of the merge or relevant commit in the META/ISSUES/ISSUE_X/README.md file.
- Before deleting a feature branch (locally or remotely), proof MUST exist that the branch is fully merged to main. This can be shown by:
  - The branch's tip commit is reachable from main (e.g., via `git log main` or `git merge-base --is-ancestor <branch> main`).
  - The merge commit SHA is referenced in the META/ISSUES/ISSUE_X/README.md file for traceability.
- When an issue is marked as done, the corresponding GitHub issue MUST be closed, with a comment referencing the commit (SHA) from the issue description.

## Issue File Organization

- All issue documentation MUST be stored in `META/ISSUES/OPEN/ISSUE_X` for open issues, and moved to `META/ISSUES/CLOSED/ISSUE_X` when closed, where X is the GitHub issue number.
- Each issue directory MUST contain a README.md file with the description, status, and other relevant information.
- For bugs or technical issues, relevant reproduction steps, test files, or other artifacts should be stored in the issue directory.

## Notes

- **Note:** In this project, we do **not** use pull requests for merging feature branches. All merges are performed locally by the developer, and traceability is maintained via commit SHAs and META updates. This is a project-specific policy for efficiency and clarity.
