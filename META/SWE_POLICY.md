# Software Engineering Policy: Feature Development Workflow

## Policy Statement

When developing a new feature in this repository, the following workflow is mandatory:

0. **Pre-Branching Cleanup**

   - Before creating any branch, always commit and clean up the working directory.
   - Ensure the main branch is up to date and clean before branching for a new feature.

1. **Create a Task Description in META (Main Branch)**

   - Write a formal task description in the META directory on the main branch.
   - This serves as the authoritative record of the feature's requirements and scope.

2. **Create a GitHub Issue**

   - Open a new issue in the repository describing the feature to be developed.
   - Reference the META task description (file path and title) in the issue.
   - The issue should be clear, concise, and provide sufficient context for the feature.

3. **Reference the GitHub Issue in the Task Description**

   - Update the META task description in the main branch to include a link to the GitHub issue.

4. **Create a Feature Branch**

   - Create a new branch from the default branch (usually `main` or `master`).
   - The branch name must be clear, unambiguous, and related to the issue.
   - **Best Practice:** Include the GitHub issue number in the branch name for traceability. For example: `feature/123-add-user-authentication` where `123` is the issue number.
   - Add a link to the feature branch in the GitHub issue.

5. **Switch to the Feature Branch**

   - Ensure all development for the feature (including design documents and implementation) is performed on this branch.

6. **Follow Best Practices**
   - Keep the branch focused on the feature described in the issue.
   - Reference the issue in commit messages and pull requests.
   - Regularly push changes to the remote branch.
   - Open a pull request referencing the issue when the feature is ready for review.

## Rationale

This policy ensures traceability, clarity, and alignment with industry best practices for collaborative software development. The main branch always contains the authoritative task description, which links to the issue, and the issue links to the feature branch, ensuring seamless navigation and context for all contributors.
