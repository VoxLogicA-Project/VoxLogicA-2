# META Directory Guide

## Purpose

The `META` directory contains all records, policies, and documentation related to the software engineering (SWE) process, requirements, tasks, and issues for this project. It serves as the central location for process management and project organization artifacts.

## Usage

- **Policies:** SWE policies and best practices are documented here (e.g., `SWE_POLICY.md`).
- **Issues:** All issues are organized in `META/ISSUES/OPEN` (for open issues) and `META/ISSUES/CLOSED` (for closed issues), with directories named using descriptive kebab-case names **mandatory** prefixed with date in yyyy-mm-dd format. Each issue directory contains a README.md file and any other relevant files for that issue. 

DO NOT PUT ANY FILES DIRECTLY IN THE `META/ISSUES/OPEN` OR `META/ISSUES/CLOSED` DIRECTORIES. ALWAYS CREATE A NEW DIRECTORY FOR EACH ISSUE.

## Recent Important Changes

### Closure-Based For-Loop Implementation (Jun 2025)
Successfully implemented robust closure-based for-loops enabling distributed execution:
- **ClosureValue Enhancement**: Proper AST Expression and Environment capture instead of string-based approach
- **Direct Operation Execution**: Bypasses full execution engine for efficiency, with proper argument mapping
- **Distributed Execution**: For-loops now work correctly in Dask workers with proper variable scoping
- **Graceful Fallback**: Complex dependency scenarios handled with fallback mechanisms
- **Result**: `./voxlogica run --no-cache test_simpleitk.imgql` now executes successfully with meaningful results

### Storage Architecture Enhancement (Dec 2024)
The VoxLogicA storage backend was enhanced to handle both serializable and non-serializable results:
- **Serializable results** (numbers, strings, certain images) → Persistent SQLite database
- **Non-serializable results** (closures, complex objects) → Memory cache
- This resolved critical serialization errors in distributed execution (dask_map) while maintaining backward compatibility.

## AI Responsibility

- The AI is responsible for keeping the `META` directory and this guide up to date, concise, and free of redundancy.
- The AI must ensure all relevant process changes, new policies, and important records are reflected here promptly.
