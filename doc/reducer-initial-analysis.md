# Initial Analysis: Reducer.fs (VoxLogicA-2)

## Purpose

The `Reducer` module is the core logic engine of VoxLogicA-2. It transforms parsed ImgQL expressions and commands into an optimized, deduplicated execution plan (WorkPlan) consisting of operations and goals.

## Key Concepts

- **Operator**: Atomic element (identifier, number, bool, string)
- **Operation**: Operator applied to arguments (dict with string numeric keys mapping to operation IDs)
- **Goal**: High-level action (Save, Print)
- **WorkPlan**: All operations and goals, ready for execution
- **Environment**: Variable/function bindings, supports closures
- **Memoization**: Ensures unique operations, avoids recomputation
- **Import**: Supports modular ImgQL files, prevents duplicate imports

## Core Logic

- **Operation Deduplication**: Uses dictionaries to ensure identical operations are only created once (memoization).
- **Environment and Binding**: Implements variable/function bindings and closures.
- **Reduction Process**: Recursively processes commands and expressions, updating environment and operations.
- **Import Handling**: Supports importing other ImgQL files, searching both current and standard library directories.
- **Output Generation**: Can convert the work plan to an executable program or DOT graph for visualization.

## Design Features

- Functional and modular structure
- Extensible for new operators, commands, or expression types
- Efficient via memoization and deduplication

## Operation Arguments Structure (Updated)

As of ISSUE_DAG_DICT_ARGS implementation:

- **Arguments Type**: Changed from `List[OperationId]` to `Dict[str, OperationId]`
- **Key Format**: String representations of argument positions ("0", "1", "2", etc.)
- **Backwards Compatibility**: Parser and language remain unchanged; only reducer and downstream DAG representation affected
- **Benefits**: Improved extensibility, clarity, and future-proofing for named arguments

## Summary Table

| Concept     | Description                                                           |
| ----------- | --------------------------------------------------------------------- |
| Operator    | Atomic element (identifier, number, bool, string)                     |
| Operation   | Operator + arguments (dict with string numeric keys to operation IDs) |
| Goal        | High-level action (Save, Print)                                       |
| WorkPlan    | All operations and goals, ready for execution                         |
| Environment | Variable/function bindings, supports closures                         |
| Memoization | Ensures unique operations, avoids recomputation                       |
| Import      | Supports modular ImgQL files, prevents duplicate imports              |
| Output      | Can generate executable program or DOT graph for visualization        |

## Conclusion

`Reducer.fs` is the core of VoxLogicA-2's logic engine, responsible for transforming parsed ImgQL code into an optimized, executable plan. It is well-structured for modularity, extensibility, and efficiency, and is a strong foundation for further development or language migration. The recent update to dict-based arguments improves the DAG structure for future extensibility while maintaining parser compatibility.
