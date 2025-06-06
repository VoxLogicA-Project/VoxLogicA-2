# AGENT INSTRUCTIONS

Directories:

META: SWE tasks and such, related to the process of producing VoxLogica2. Dev documentation as such is part of the artifact and currently in the doc directory.

tests: all test-related infrastructure

implementation: implementation

# WHAT **NOT** TO DO

- Do not write tests OUTSIDE of the tests directory

- Do not write tests randomly in the test directory: the testing infrastructure is well documented and new tests go there

- Do not report issues at random: create a folder in the OPEN directory, add a README and all useful material, link issues to tests and tests to issues

# WHAT TO DO INSTEAD

All files in META are important. GUIDE.md is the main guide.

MANDATORY: For any request I make in chat about requirements, tasks, issues, whatever, make a formal record in META. The structure is flexible, emergent, do not prepare templates, scripts or set boundaries, just operate the META dir as if you were a senior software engineer and developer.

MANDATORY: At the start of a new chat, confirm you know all the rules by writing READY so I know you read until this last line.

MANDATORY: The AI is responsible for keeping the META directory and GUIDE.md up to date, concise, and free of redundancy.

MANDATORY: In any chat, when executing any command, it is required to follow the policies in the META directory and all SWE decisions therein.

MANDATORY: The AI is responsible for checking and maintaining the documentation (in doc/) so that it is accurate with respect to the current implementation, and must act on changes appropriately.

MANDATORY: The AI MUST always verify its answers before responding to any user question. This is to be treated as a core agent policy.
