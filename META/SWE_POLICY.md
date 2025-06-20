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

## MANDATORY Test Infrastructure Policies

### Test Organization and Structure

1. **Location Requirements:**
   - ALL tests MUST be located within the `tests/` directory
   - NO tests shall be created in the root directory or other locations
   - Each test MUST be in its own subdirectory following the pattern `test_[feature_name]/`

2. **Directory Structure:**
   - Each test directory MUST contain an `__init__.py` file
   - Primary test file MUST be named `test_[feature_name].py` or match the directory name
   - Test-specific data or utilities should be contained within the test directory

3. **Test File Standards:**
   - Every test file MUST include a `description` variable explaining the test's purpose
   - Tests MUST follow the established template pattern (see `tests/README.md`)
   - Tests MUST handle command-line arguments appropriately (especially `--language`)
   - Tests MUST provide proper exit codes (0 for success, 1 for failure)

4. **Integration Requirements:**
   - New tests MUST be added to the `TEST_MODULES` list in `tests/run_tests.py`
   - Tests MUST be executable both individually and through the test runner
   - Tests MUST use the established test infrastructure utilities where appropriate

### Issue-Test Linking Requirements

1. **Mandatory Cross-References:**
   - When a test is created to address an issue, the issue's README.md MUST reference the test
   - The test's description MUST reference the corresponding issue
   - Use format: `META/ISSUES/[OPEN|CLOSED]/[issue-directory-name]`

2. **Test Descriptions for Issue-Related Tests:**
   ```python
   description = """Test for issue META/ISSUES/OPEN/2025-06-20-feature-name:
   Brief description of what this test validates in relation to the issue."""
   ```

3. **Issue Documentation:**
   - Issues MUST document their associated tests in their README.md
   - Include test execution instructions and expected outcomes
   - Link test logs and results when relevant

### Test Infrastructure Maintenance

1. **Documentation Updates:**
   - The `tests/README.md` MUST be kept current with any infrastructure changes
   - Test infrastructure utilities MUST be properly documented
   - Examples and usage patterns MUST be maintained

2. **Consistency Enforcement:**
   - All existing tests MUST be migrated to follow the established patterns
   - No legacy test files shall remain outside the proper directory structure
   - Test naming and organization MUST be consistent across the codebase

3. **Quality Assurance:**
   - Tests MUST be reviewed for compliance with these policies
   - The test infrastructure MUST be verified after any structural changes
   - Test coverage and effectiveness MUST be monitored and maintained
