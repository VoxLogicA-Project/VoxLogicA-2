# Software Engineering Policy: Feature Development Workflow

## Policy Statement

When developing a new feature in this repository, the following workflow is mandatory:

0. **Pre-Branching Cleanup**

   - Before creating any branch, the working directory MUST be clean: there must be no active changes (no uncommitted or unstaged files).
   - Always switch to the main branch, ensure it is up to date and clean, and only then proceed to create a new branch.

1. **Create a GitHub Issue**

   - Open a new issue in the repository describing the feature to be developed.
   - Reference the META task description (file path and title) in the issue.
   - The issue should be clear, concise, and provide sufficient context for the feature.

2. **Reference the GitHub Issue in the Task Description**

   - Update the META task description in the main branch to include a link to the GitHub issue.

3. **Create a Feature Branch**

   - Create a new branch from the default branch (usually `main` or `master`).
   - The branch name must be clear, unambiguous, and related to the issue.
   - **Best Practice:** Include the GitHub issue number in the branch name for traceability. For example: `feature/123-add-user-authentication` where `123` is the issue number.
   - Add a link to the feature branch in the GitHub issue.

4. **Switch to the Feature Branch**

   - Ensure all development for the feature (including design documents and implementation) is performed on this branch.

5. **Follow Best Practices**
   - Keep the branch focused on the feature described in the issue.
   - Reference the issue in commit messages and pull requests.
   - Regularly push changes to the remote branch.
   - Open a pull request referencing the issue when the feature is ready for review.

- All file and directory renames for versioned files (i.e., tracked by git) must be performed using git commands (e.g., 'git mv'), not by using 'mv' or other filesystem-only renaming methods. This ensures proper tracking of file history and avoids confusion in version control.

## Rationale

This policy ensures traceability, clarity, and alignment with industry best practices for collaborative software development. The main branch always contains the authoritative task description, which links to the issue, and the issue links to the feature branch, ensuring seamless navigation and context for all contributors.
