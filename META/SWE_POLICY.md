# Software Engineering Policy: Feature Development Workflow

## Policy Statement

When developing a new feature in this repository, the following workflow is mandatory:

0. **Pre-Branching Cleanup**

   - Before creating any branch, the working directory MUST be clean: there must be no active changes (no uncommitted or unstaged files).
   - Always switch to the main branch, ensure it is up to date and clean, and only then proceed to create a new branch.

1. **Create a GitHub Issue**

   - Open a new issue in the repository describing the feature to be developed.
   - The issue should be clear, concise, and provide sufficient context for the feature.

2. **Create a Feature Branch**

   - Create a new branch from the default branch (usually `main` or `master`).
   - The branch name must be clear, unambiguous, and related to the issue.
   - **Best Practice:** Include the GitHub issue number in the branch name for traceability. For example: `feature/123-add-user-authentication` where `123` is the issue number.

3. **Switch to the Feature Branch**

   - Ensure all development for the feature is performed on this branch.

4. **Follow Best Practices**
   - Keep the branch focused on the feature described in the issue.
   - Reference the issue in commit messages and pull requests.
   - Regularly push changes to the remote branch.
   - Open a pull request referencing the issue when the feature is ready for review.

## Rationale

This policy ensures traceability, clarity, and alignment with industry best practices for collaborative software development.
