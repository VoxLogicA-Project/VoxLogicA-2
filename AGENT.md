# AGENT INSTRUCTIONS

Directories:

META: SWE tasks and such, related to the process of producing VoxLogica2. Dev documentation as such is part of the artifact and currently in the doc directory.

tests: all test-related infrastructure

implementation: implementation

Unless specifically instructed otherwise, do not create new directories or files outside of the tests, meta or implementation directories. It is forbidden to create new directories or files in the root directory or in places different from the ones above.

Coding conventions:
- Use Python 3.11+ syntax and features.
- Use type hints and docstrings for all functions and classes.
- Use the type keyword for type hints, use "|" instead of Union

Git diff:
- git diff uses a pager by default, so use git diff --no-pager to see the output directly in the terminal.

# MANDATORY POLICIES

- Do not run arbitrary credible commands to run tests or execute the tool: use the two main scripts in tests and in the root dir of the repo. They take care of loading the correct venv

- Do not create venvs: they are already there.

- Do not write tests OUTSIDE of the tests directory

- Do not write tests randomly in the test directory: the testing infrastructure is well documented and new tests go there

- Do not report issues at random: create a folder in the OPEN directory, add a README and all useful material, link issues to tests and tests to issues

# CODING POLICIES

- Do not use timeouts unless absolutely justified and unavoidable. Prefer deterministic completion detection over timeout-based mechanisms.

- Do not use locks unless absolutely justified and unavoidable. Prefer lock-free atomic operations and race-condition-aware algorithms.

- Always prefer event-driven or future-based waiting over polling or timeout-based waiting.

# WHAT TO DO INSTEAD

All files in META are important. GUIDE.md is the main guide.

MANDATORY: For any request I make in chat about requirements, tasks, issues, whatever, make a formal record in META. The structure is flexible, emergent, do not prepare templates, scripts or set boundaries, just operate the META dir as if you were a senior software engineer and developer.

MANDATORY: At the start of a new chat, confirm you know all the rules by writing READY so I know you read until this last line.

MANDATORY: The AI is responsible for keeping the META directory and GUIDE.md up to date, concise, and free of redundancy.

MANDATORY: Do not record temporary information into important files such as GUIDE.md or SWE_POLICY.md. We are not keeping temporary records by now, except for the issues in the OPEN directory, which are not temporary but rather open issues.

MANDATORY: In any chat, when executing any command, it is required to follow the policies in the META directory and all SWE decisions therein.

MANDATORY: The AI is responsible for checking and maintaining the documentation (in doc/) so that it is accurate with respect to the current implementation, and must act on changes appropriately.

MANDATORY: The AI MUST always verify its answers before responding to any user question. This is to be treated as a core agent policy.

MANDATORY: The AI must read README.md in the root directory at the start of each new chat. This file describes the tool usage and conventions for running and testing VoxLogicA.

MANDATORY: The AI must read STATUS.md in the root directory at the start of each new chat and summarize its contents in the first message. This file contains the current development status, priorities, and next steps for the VoxLogicA-2 project.

MANDATORY: For all testing and usage, the AI must use the main executable script (./voxlogica) as an end user would, not by invoking Python modules directly (e.g., not with python -m or similar). All CLI and API usage must follow the documented user workflow.
