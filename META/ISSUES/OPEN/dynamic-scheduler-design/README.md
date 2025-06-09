# Dynamic Scheduler with On-DB Memory Allocation Design

## Issue Summary
Design an execution semantics for VoxLogica-2 that implements a dynamic scheduler with database-backed memory allocation for DAG node results.

## Requirements
- DAG nodes have SHA256 IDs for unique identification
- Computed node results must be persisted to database
- Support for multiple data types: strings, numbers, booleans, files (as blobs)
- High-performance storage and retrieval requirements
- Serverless database preferred (SQLite, JSON-based)
- Optional memory-mapped file optimization for large blob data
- In general, employ a zero-copy approach even for writing to the database: ideally nodes write directly to it

## Context
This is part of the VoxLogica-2 execution engine development, focusing on efficient caching and memory management for large-scale image processing workflows.

## Status
Open - Design phase

## Related Files
- `/doc/dev/dynamic-scheduler/PROMPT.md` - Original requirement specification
- `/doc/dev/SEMANTICS.md` - Overall language semantics
