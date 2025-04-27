# F# Stack Overflow Issue in Reducer

## Description

When running a function-based imgql file with operations that cause combinatorial explosion in the DAG, the F# implementation crashes with a stack overflow. This happens when using function declarations with parameters and complex arithmetic operations.

## Reproducing the Issue

The issue can be reproduced using the attached test file `function_explosion_failure.imgql`. This file includes function declarations with arithmetic operations on parameters (using +, -, \*, /), which appear to cause excessive recursion in the reducer.

Run the test with:

```bash
dotnet run --project implementation/fsharp/VoxLogicA.fsproj tests/function_explosion_failure.imgql
```

## Error Log

```
[info] VoxLogicA version: 1.0.0+f5ed65d6050703df3c511e86827d2e6da2be84ce
[dbug] Program parsed
```

Followed by a lengthy stack trace showing recursive calls to `reduceExpr`, `reduceArgs`, and related methods, eventually leading to a stack overflow. The error is triggered during the reduction phase when processing function declarations with multiple arithmetic operations on parameters.

## Severity

Medium - This issue prevents complex function-based imgql files from running, but it does not affect the core functionality for simpler cases.

## Possible Causes

The issue likely occurs due to:

1. Excessive recursion in the reducer when handling complex nested function calls with parameter manipulation
2. Lack of tail-call optimization or memoization for certain recursive patterns
3. Combinatorial explosion of operations due to repeated evaluation of expressions with different parameter values

## Notes

A simpler version of the function explosion test (without arithmetic operations in parameters) does work correctly, producing 96 operations. This suggests the issue is related to how parameter manipulation is handled in function calls.

## GitHub Issue Reference

This issue is tracked in GitHub issue: https://github.com/VoxLogicA-Project/VoxLogicA-2/issues/4
