# Algorithms: Lifetime Analysis and Buffer Assignment

This document describes algorithms for analyzing the lifetimes of outputs in a DAG and assigning buffers to nodes for static memory reuse.

## Contents
- Lifetime analysis: determining when each output is last used
- Buffer assignment: mapping nodes to buffer IDs, ensuring no overlap and type/shape compatibility
- Example pseudocode and worked examples

See also: `overview.md` for motivation, `integration.md` for framework notes, and `references.md` for related work.
