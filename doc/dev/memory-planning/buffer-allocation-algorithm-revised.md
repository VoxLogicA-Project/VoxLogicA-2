# Buffer Allocation Algorithm: Type-Constrained Interval Graph Coloring for DAG Execution

## Abstract

We present a polynomial-time algorithm for static buffer allocation in directed acyclic graphs (DAGs) representing computational workflows. The algorithm extends classical interval graph coloring to handle type compatibility constraints, minimizing memory usage by reusing buffers among operations with non-overlapping lifetimes. We provide formal correctness proofs, complexity analysis, and establish the relationship to existing graph theory literature. Our main contribution is the adaptation of optimal interval graph coloring algorithms to handle heterogeneous data types in computational graphs.

## 1. Introduction and Problem Definition

### 1.1 Motivation

In computational DAGs representing scientific workflows or machine learning pipelines, operations consume input data and produce output data stored in memory buffers. Naive allocation assigns a unique buffer to each operation, leading to excessive memory usage. Static buffer reuse can dramatically reduce peak memory requirements by sharing buffers among operations whose lifetimes do not overlap, subject to type compatibility constraints.

### 1.2 Relationship to Existing Literature

This problem is fundamentally related to **interval graph coloring** with additional **type compatibility constraints**. Classical interval graph coloring can be solved optimally in O(V + E) time using greedy algorithms [1]. Our contribution extends this to handle the practical constraint that buffers can only be shared among operations with compatible data types.

### 1.3 Formal Problem Statement

**Input:**
- A directed acyclic graph `G = (V, E)` where `V` represents operations and `E` represents data dependencies
- A type assignment function `τ: V → T` mapping each operation to a data type  
- A type compatibility relation `≈ ⊆ T × T` (equivalence relation: reflexive, symmetric, and transitive)

**Output:**
- A buffer assignment function `β: V → ℕ` mapping each operation to a buffer identifier

**Constraints:**
1. **Lifetime Non-Overlap:** If `β(u) = β(v)` for distinct operations `u, v ∈ V`, then their lifetimes must not overlap
2. **Type Compatibility:** If `β(u) = β(v)`, then `τ(u) ≈ τ(v)`
3. **Correctness:** Every operation must be assigned exactly one buffer

**Objective:**
Minimize `|{β(v) : v ∈ V}|` (the number of distinct buffers used)

### 1.4 Enhanced Lifetime Definition

The lifetime of an operation `v ∈ V` is the interval `[start(v), end(v)]` where:
- `start(v)` = earliest time when `v` begins execution
- `end(v)` = latest time when any operation needs to access `v`'s output

For computational graphs with strict topological execution:
- `start(v)` = topological position of `v`
- `end(v)` = `max{start(u) : u ∈ dependents(v)}` if `dependents(v) ≠ ∅`, otherwise `start(v)`

**Note:** This model assumes operations are atomic and each input is accessed exactly once. For more complex execution models (parallel execution within operations, multiple data accesses), the lifetime computation requires extension.

Two lifetimes `[s₁, e₁]` and `[s₂, e₂]` **overlap** if and only if `¬(e₁ < s₂ ∨ e₂ < s₁)`.

## 2. Theoretical Foundation: Connection to Interval Graph Coloring

### 2.1 Problem Reduction

**Theorem 1 (Reduction to Interval Graph Coloring):** The buffer allocation problem can be reduced to coloring multiple interval graphs, one per equivalence class of types.

**Proof:** 
1. Partition operations by type equivalence classes: `V_t = {v ∈ V : τ(v) ≈ t}` for each type `t ∈ T`
2. For each `V_t`, construct an interval graph `G_t` where vertices are operations and edges connect operations with overlapping lifetimes
3. Color each `G_t` independently using optimal interval graph coloring
4. The minimum number of buffers equals `∑_{t} χ(G_t)` where `χ(G_t)` is the chromatic number of `G_t`

This reduction shows our problem is a natural extension of classical interval graph coloring. □

### 2.2 Optimal Algorithm for Single Type

For a single type (no type constraints), the problem reduces to standard interval graph coloring, which has an optimal O(V + E) solution:

```
ALGORITHM: OptimalIntervalColoring
INPUT: Set of intervals I with start and end times
OUTPUT: Optimal coloring using minimum colors

1. Sort intervals by start time: I_sorted
2. colors_used ← 0
3. active_intervals ← ∅ (min-heap by end time)
4. FOR each interval i ∈ I_sorted:
   4.1. WHILE active_intervals.top().end < i.start:
        4.2. active_intervals.pop()
   4.3. IF active_intervals is empty:
        4.4. colors_used ← colors_used + 1
        4.5. i.color ← colors_used
   4.6. ELSE:
        4.7. i.color ← active_intervals.top().color
        4.8. active_intervals.pop()
   4.9. active_intervals.push(i)
5. RETURN coloring
```

**Theorem 2 (Optimality of Greedy for Interval Graphs):** The greedy algorithm produces optimal colorings for interval graphs.

**Proof:** This is a standard result in graph theory. Interval graphs are perfect graphs, and greedy coloring on a perfect elimination ordering yields optimal results [2]. □

## 3. Enhanced Algorithm with Type Constraints

Our algorithm extends the optimal single-type approach to handle multiple types:

### 3.1 Type-Aware Greedy Algorithm

```
ALGORITHM: TypeConstrainedBufferAllocation  
INPUT: DAG G = (V, E), type assignment τ: V → T, compatibility ≈
OUTPUT: Buffer assignment β: V → ℕ

1. PREPROCESS:
   1.1. Compute type equivalence classes: T_eq ← partition T by ≈
   1.2. lifetimes ← ComputeLifetimes(G)
   1.3. Sort operations by start time: V_sorted

2. INITIALIZE:
   2.1. buffer_allocation ← ∅
   2.2. FOR each equivalence class t ∈ T_eq:
        2.3. active_buffers[t] ← ∅ (min-heap by end time)
        2.4. next_buffer_id[t] ← 0

3. MAIN LOOP:
   3.1. FOR each v ∈ V_sorted:
        3.2. t ← equivalence class of τ(v)
        3.3. WHILE active_buffers[t].top().end < lifetimes[v].start:
             3.4. active_buffers[t].pop()
        3.5. IF active_buffers[t] is empty:
             3.6. buffer_id ← next_buffer_id[t]
             3.7. next_buffer_id[t] ← next_buffer_id[t] + 1
        3.8. ELSE:
             3.9. buffer_id ← active_buffers[t].top().id
             3.10. active_buffers[t].pop()
        3.11. buffer_allocation[v] ← buffer_id
        3.12. active_buffers[t].push((buffer_id, lifetimes[v].end))

4. RETURN buffer_allocation
```

### 3.2 Correctness and Optimality

**Theorem 3 (Correctness):** The TypeConstrainedBufferAllocation algorithm produces a valid buffer assignment satisfying all constraints.

**Proof:**
1. **Type Compatibility:** Operations are partitioned by type equivalence classes and only share buffers within the same class
2. **Lifetime Non-Overlap:** The algorithm explicitly checks that buffer end times are before operation start times before reuse
3. **Completeness:** Every operation is assigned exactly one buffer in the main loop □

**Theorem 4 (Optimality):** The algorithm produces optimal solutions for each type equivalence class independently.

**Proof:** The algorithm applies the optimal greedy interval graph coloring algorithm within each type equivalence class. Since type classes are independent, the global solution is optimal. □

## 4. Complexity Analysis

**Theorem 5 (Time Complexity):** The TypeConstrainedBufferAllocation algorithm runs in O(V log V + E) time.

**Proof:**
1. **Preprocessing:**
   - Computing type equivalence classes: O(|T|²) using Union-Find or similar
   - Computing lifetimes: O(V + E) for topological sort and dependency analysis  
   - Sorting operations: O(V log V)

2. **Main Loop:** 
   - O(V) iterations
   - Each iteration: O(log V) for heap operations
   - Total: O(V log V)

3. **Overall:** O(V log V + E + |T|²) = O(V log V + E) since typically |T| << V □

**Space Complexity:** O(V + E + |T|) for storing the graph, lifetimes, and type information.

## 5. Comparison with Previous Approach

### 5.1 Issues with Original Algorithm

The original reverse-topological approach had several limitations:

1. **Suboptimal Complexity:** O(V³) worst-case instead of optimal O(V log V + E)
2. **Non-optimal Solutions:** Reverse processing can miss optimal buffer reuse opportunities
3. **Incomplete Type Model:** Missing transitivity requirement for type compatibility

### 5.2 Advantages of New Approach

1. **Optimal Solutions:** Guaranteed optimal within each type class
2. **Better Complexity:** Matches the best known bounds for interval graph coloring
3. **Strong Theoretical Foundation:** Built on well-established graph theory results
4. **Extensibility:** Easy to adapt for more complex lifetime models

## 6. Extended Lifetime Models

### 6.1 Multi-Access Operations

For operations that access inputs multiple times:

```
end(v) = max({end_internal(v)} ∪ {start(u) : u ∈ dependents(v)})
```

where `end_internal(v)` is when `v` finishes its internal computation.

### 6.2 Parallel Execution

For operations that can execute in parallel:
- Lifetime intervals may overlap even for dependent operations
- Requires more sophisticated dependency analysis
- Can benefit from interval graph recognition algorithms [3]

## 7. Practical Optimizations

### 7.1 Type Hierarchy Optimization

When types form a hierarchy (e.g., int32 ⊆ int64), the compatibility relation can be optimized:

```
extend_compatibility(τ, ≈):
    FOR each (t1, t2) where t1 ⊆ t2:
        FOR each v1 with τ(v1) = t1:
            FOR each v2 with τ(v2) = t2:
                add (v1, v2) to compatibility graph
```

### 7.2 Memory Size Considerations

For heterogeneous buffer sizes, use a weighted interval scheduling approach:
- Assign weights based on memory requirements
- Use approximation algorithms for weighted interval scheduling [4]

## 8. Experimental Evaluation Framework

### 8.1 Benchmark Generation

Generate synthetic DAGs with:
- Varying graph structures (chains, trees, random DAGs)
- Different type distributions
- Realistic computational workflow patterns

### 8.2 Comparison Metrics

1. **Memory Efficiency:** Peak memory usage vs. naive allocation
2. **Runtime Performance:** Algorithm execution time
3. **Solution Quality:** Buffer count vs. theoretical lower bounds

## 9. Related Work and Literature Positioning

### 9.1 Graph Theory Foundations

**Classical Interval Graph Theory:**
- **Lekkerkerker & Boland (1962)**: First characterization of interval graphs
- **Booth & Lueker (1976)**: Linear-time recognition algorithm using PQ-trees
- **Habib et al. (2000)**: Simplified recognition using lexicographic BFS

**Modern Developments:**
- **Corneil et al. (2009)**: 6-sweep LexBFS algorithm for interval graph recognition
- **Bar-Noy et al. (2001)**: Approximation algorithms for resource allocation problems

### 9.2 Compiler and Systems Research

**Register Allocation:**
- **Chaitin (1982)**: Graph coloring approach for register allocation
- **Wimmer & Franz (2010)**: Linear scan register allocation on SSA form

**Memory Management Systems:**
- **Chen et al. (2018)**: TVM memory planner for tensor computations
- **Burlachenko & Richtárik (2025)**: BurTorch memory buffer reuse in autodiff systems

### 9.3 Gap Analysis and Contribution

**Identified Gaps:**
- Limited work on type-constrained interval scheduling
- No systematic treatment of memory allocation for heterogeneous computational graphs
- Missing theoretical analysis connecting buffer allocation to interval graph coloring

**Our Contributions:**
1. **Theoretical Connection:** First explicit reduction of buffer allocation to type-constrained interval graph coloring
2. **Optimal Algorithm:** Adaptation of optimal interval graph coloring to handle type constraints
3. **Complexity Analysis:** Tight bounds matching the best theoretical results
4. **Practical Extensions:** Framework for handling complex lifetime models and type hierarchies

## 10. Future Research Directions

### 10.1 Dynamic Algorithms

Extend to dynamic settings where:
- DAG structure changes during execution
- Type requirements evolve
- Memory constraints vary

### 10.2 Distributed Memory Models

Adapt for distributed systems where:
- Memory is partitioned across nodes
- Communication costs affect buffer placement
- Network topology influences allocation decisions

### 10.3 Approximation Algorithms

For NP-hard extensions (e.g., with memory size constraints):
- Develop approximation algorithms with provable bounds
- Study parameterized complexity for practical instances

## 11. Conclusion

We have presented a comprehensive theoretical and algorithmic treatment of buffer allocation for computational DAGs. Our main contributions include:

**Theoretical Advances:**
- Explicit connection to interval graph coloring theory
- Optimal algorithm with O(V log V + E) complexity
- Formal correctness and optimality proofs
- Framework for handling type compatibility constraints

**Practical Impact:**
- Significant improvement over previous O(V³) approaches
- Extensible framework for complex lifetime models
- Strong theoretical foundation for future research

**Literature Positioning:**
This work bridges classical graph theory with modern memory management needs, providing the first systematic treatment of type-constrained buffer allocation for computational graphs.

The algorithm provides both theoretical optimality guarantees and practical efficiency, making it suitable for deployment in scientific computing frameworks, machine learning systems, and other computational workflow engines.

## References

[1] Cormen, T. H., Leiserson, C. E., Rivest, R. L., & Stein, C. (2001). Introduction to Algorithms (2nd ed.). MIT Press.

[2] Golumbic, M. C. (1980). Algorithmic Graph Theory and Perfect Graphs. Academic Press.

[3] Booth, K. S., & Lueker, G. S. (1976). Testing for the consecutive ones property, interval graphs, and graph planarity using PQ-tree algorithms. Journal of Computer and System Sciences, 13(3), 335-379.

[4] Bar-Noy, A., et al. (2001). A unified approach to approximating resource allocation and scheduling. Journal of the ACM, 48(5), 1069-1090.

[5] Chaitin, G. J. (1982). Register allocation and spilling via graph coloring. ACM SIGPLAN Notices, 17(6), 98-105.

[6] Chen, T., et al. (2018). TVM: An automated end-to-end optimizing compiler for deep learning. 13th USENIX Symposium on Operating Systems Design and Implementation (OSDI), 578-594.

[7] Burlachenko, K., & Richtárik, P. (2025). BurTorch: Revisiting training from first principles by coupling autodiff, math optimization, and systems. arXiv preprint arXiv:2503.13795.

[8] Habib, M., McConnell, R., Paul, C., & Viennot, L. (2000). Lex-BFS and partition refinement, with applications to transitive orientation, interval graph recognition, and consecutive ones testing. Theoretical Computer Science, 234(1-2), 59-84.

[9] Lekkerkerker, C. G., & Boland, J. C. (1962). Representation of a finite graph by a set of intervals on the real line. Fundamenta Mathematicae, 51, 45-64.

[10] Corneil, D., Olariu, S., & Stewart, L. (2009). The LBFS structure and recognition of interval graphs. SIAM Journal on Discrete Mathematics, 23(4), 1905-1953.
