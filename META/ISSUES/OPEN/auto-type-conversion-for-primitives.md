# VoxLogicA-2 Issue: Type auto-conversion for primitive arguments

## Problem
Many primitives (e.g., index, simpleitk filters) require arguments to be of a specific type (e.g., int for indices), but VoxLogicA-2 sometimes passes floats or strings. This leads to runtime errors like 'tuple indices must be integers or slices, not float'.

## Solution
Implement a global, centralized type auto-conversion system for primitive arguments, so that each primitive does not need to handle type conversion individually.

## Context
- Similar issues have been observed with SimpleITK filters and other primitives.
- Temporary fixes are being applied per primitive, but a global solution is needed.

## Action
- [ ] Design and implement a global type auto-conversion mechanism for primitive arguments.
- [ ] Refactor existing primitives to remove per-primitive conversion logic once global solution is in place.

---
Created automatically by GitHub Copilot on 2025-06-11.
