# Every reference is a handle

Design for VoxLogicA-2 issue #52. Branch `handles`.

## 1. Why

A 309-case nnU-Net training was OOM-killed after six hours (#51). The engine had
held **51.4 GB unreleasable since second 30**, at pressure above its own hard
ceiling, with one node in flight and nothing it could evict.

`for g in train do triple(g)` expands into 309 independent nodes; a
`default.sequence` node then gathers them into one Python list. Building that
list requires all 309 volumes resident at the same instant, and the list is then
the argument of a kernel that runs for ten hours.

Two facts make this a design defect rather than a large workload:

- The volumes came from 2.7 GB of `.nii.gz` and were written back as 3.0 GB of
  `.nii.gz`. The 51.4 GB in the middle is a decompression nothing needed.
- `default.sequence`'s kernel is `[value for _index, value in ordered]`. It never
  looks at a volume. It was handed 51 GB in order to build a list.

The cause is one line, `engine/executor.py:252`:

```python
args = [_unwrap(lookup(arg_id)) for arg_id in node.args]
```

Every argument of every kernel is resolved to a value before the kernel runs.

## 2. The change, in one sentence

**A node's arguments are its inputs' merkle hashes; operators that want values
get them through an adapter that does exactly what happens today, and operators
that do not want values keep the hashes.**

## 3. Representation

```python
@dataclass(frozen=True, slots=True)
class Handle:
    node: NodeId
```

A handle is a tagged merkle hash and nothing else. It carries no resolver, no
table reference, no cached value. It is therefore:

- **a value**: serializable, content-addressable, safe to put inside another
  value and persist;
- **process-independent**: the same handle means the same thing in a later run;
- **distinguishable**: the tag is what separates a list of hashes from a list of
  strings, which a bare `NodeId` could not.

Resolution is not a method on `Handle`. Resolution is what the cache hierarchy
already does for `table.values` misses: live tier, then store, then recompute.

## 4. Three argument modes, and one orthogonal axis

### Eager — the default, and unchanged

An operator that declares nothing is eager. The adapter is the line that already
exists, with one addition: it resolves **into containers**, because a lazy
producer upstream may have put handles inside a value.

```python
args = [_resolve_deep(lookup(arg_id)) for arg_id in node.args]
```

For every operator that exists today, `_resolve_deep` on a value containing no
handles is an identity walk. No operator changes. No behaviour changes.

### Lazy — receives handles

A lazy operator receives `Handle` objects. It may inspect them, count them,
reorder them, put them in its output, or drop them. **It may not resolve them.**
`default.sequence` returns `[h0 … hN]` and touches nothing.

There is no `resolve(handle)` call available to a kernel, by design. A kernel
that blocks to materialize a value would put a wait inside kernel code and make
the DAG stop being the only witness of what depends on what.

### Shallow — receives the container, not its contents

CORRECTION, found by building it. The first draft of this section said `index(s,
i)` would be `lazy` and "resolves `i` -- an eager argument". That is incoherent:
under the rule above a lazy kernel resolves nothing, so it could not read `i`,
and it could not read the list either.

What `index` actually needs is a third mode: **the value, with the handles inside
it left alone.** It then subscripts a list of hashes and returns one hash. Deep
resolution would materialize all N elements to hand back one, which is issue #51
in miniature.

The mode resolves the argument ITSELF, repeatedly, and stops there: with nested
indexing the inner `index` returns a handle, so the outer one is handed a handle
where a container belongs. Measured as `'Handle' object is not subscriptable`.

`index`, `subsequence` and `slice` are shallow. `subsequence` already had this
hand-written inside `executor._compute_node` (:246-251); declaring the mode says
it once, for every operator that needs it.

### Rewrite — an axis, not a third kind of laziness

CORRECTION, found by building it. The first draft made "rewrite into nodes" the
second shape of *lazy*. It is not: the two are independent.

- `default.sequence` is **lazy and never rewrites**.
- `for_loop` and `map` **rewrite and are not lazy** -- they never see a handle.

So `rewrite` is its own declaration, alongside the argument mode:

```python
lazy    = True   # arguments arrive as handles
shallow = True   # arguments arrive as values, contents untouched
rewrite = True   # evaluating this GROWS THE GRAPH; it has no value of its own
```

And it is not "these operators have no kernel". `for_loop` HAS one
(`primitives/default/for_loop.py`), whose docstring says whose it is: *"The
strict runtime reconstructs that closure and passes it into this kernel."* The
engine never builds that closure, because the engine expands instead. What these
operators need is **special treatment**, and the defect this design ran into is
that the treatment was decided in one place while evaluation has two paths:
dispatch knows to expand, and `_rematerialize` -- the miss path -- calls the
kernel and dies on a closure argument that is `None`.

Two roads to evaluate a node, one of which forgot a case. The same shape as the
`_materialize` boundary in section 6: a second consumer nobody adapted.

The fix is therefore not a guard inside `_rematerialize` but **one place that
decides how a node is evaluated**, asked by both roads. `_EXPANDABLE` and
`_SEQUENCE_OPERATORS` stop being private name lists and become queries over the
spec, which is also what makes a NEW graph-growing operator work everywhere by
writing one word.

That in turn makes `_recomputable` principled instead of enumerated: **a node is
kernel-recomputable exactly when evaluating it does not change the graph.**
`_SEQUENCE_OPERATORS` currently answers three unrelated questions at once -- is
it recomputable, does it produce a sequence, is it structural -- and the second
already has its own field (`PrimitiveSpec.kind == "sequence"`). Collapsing them
into one list is how the case got lost.

If a lazy operator needs a value, it **rewrites itself into nodes**:

```
nnunet.train([h0 … h308], work_root, …)

    rewrites to

  write(h0)  write(h1)  …  write(h308)      309 independent nodes
      \         |                /
             train(w0 … w308, work_root)     fan-in
```

Each `write` node is eager on exactly **one** handle. They are independent: no
artificial chain, nothing forced into sequence. `train` fans them in, and its own
arguments are the writes' results — paths, not volumes.

**What bounds peak residency is the memory governor, not the shape.** Each
write's input becomes evictable the moment that write completes, so the live set
is whatever the budget admits at once, instead of the whole sequence for the
whole run. That is the entire difference: today 51.4 GB is pinned because a
single ten-hour node holds it, and no budget can touch it. It is not "one case at
a time", and claiming so would overstate the result.

This is not a new mechanism, and both halves of it already exist:

- **Growing the DAG from inside a running node**: `engine/expander.py` reduces a
  closure body once per element into new nodes and lets the scheduler pick them
  up, computing nothing itself.
- **Forwarding a node's value from the node it rewrote to**: `core.py:249` keeps
  `_alias: dict[NodeId, NodeId]`, and `_on_spliced` (:952) pins the target, waits
  for it and forwards its value.

The generalization is that **a lazy operator may return a rewrite instead of a
value**, and loop expansion becomes one instance of that rule rather than a
special case in the scheduler.

## 5. Declaring it

One field, where everything else about a primitive is already declared:

```python
PRIMITIVE_SPEC = PrimitiveSpec(
    name="sequence",
    namespace="default",
    lazy=True,          # receives handles, never values
    ...
)
```

This is a requirement, not a convenience. Laziness that is awkward to opt into
will not be opted into, and the default must stay eager so that being wrong
about an operator costs performance and never correctness.

## 6. Liveness: a handle inside a value is a reference

`graph.consumers[nid]` counts unrun consumers holding a value; the last
`release` evicts (`engine/graph.py:233-248`). Today every reference is an edge,
so the count is complete.

With handles it is not. A value that *contains* a handle refers to a node the
graph does not know about. If that node's last edge-consumer completes, its
value is evicted while a live value still names it.

This is the failure class of the buffer-pool incident: a reference outliving
what it refers to, silently.

**Rule.** When a node completes, its value is scanned for handles, and each
referenced node is retained on behalf of the completing node. When that value is
evicted or dropped, those retains are released.

The scan is the same container walk `_resolve_deep` performs and
`buffer_states()` already performs. Its depth is the nesting depth of the value,
and it cannot cycle: handles name nodes, nodes form a DAG. It runs only where a
handle can actually be:
the value of a lazy operator. An eager operator cannot invent a handle -- it
never received one -- so its completion skips the walk entirely and pays nothing.

Consequence worth stating: a handle-list that is `protected` (a goal) retains
every element for the whole run. That is what happens today too, for the same
values — no worse, and now visible.

## 7. Persistence

### What happens today

A sequence is encoded as **one JSON blob** (`sequence-json-v1`,
`pod_codec.py:218-225`), each element inlined via `to_json_native()`. Scalars are
therefore duplicated inside the blob even though each element is also a node with
its own hash.

For images `to_json_native()` **raises** (`value_model.py:298-313`), deliberately:
returning the descriptor instead was silent data loss. So `can_serialize_value`
reports the whole container unserializable and **a sequence of images never
enters the store at all**.

That is the missing half of #51. The 51.4 GB was not unspillable by policy; it
was **unwritable**. The store for that run was 2.1 MB.

The code already names the fix: *"it needs a payload per element, not one JSON
blob."*

### What handles give

A sequence of handles is a list of hashes: JSON-native, tiny, always
serializable. Each element is already a node with its own record. Sequences of
images become persistable for the first time, losslessly, with structural
sharing — an element referenced by ten sequences is stored once.

This is the Git tree/blob model: a tree lists blob hashes, blobs are stored once.
The sister project NEARBYTES uses the same shape — RFC 6962 Merkle hashing
(`nearbytes-crypto/src/crypto/merkleHash.ts`) over `blocks/<hash>.bin`,
content-addressed and append-only at the path layer, merge by union
(`meta-storage-v2.md:85,113,118`).

### Where the bytes go

SQLite cannot be the answer at the scale this is heading for. Practical ceiling
is `page_size × max_page_count` — about 4.4 TB at 4 KB pages, ~70 TB at 64 KB —
and a single BLOB is capped near 2.1 GB, 1 GB by default compile options. Large
blobs in the db also tax vacuum, backup and concurrent writers.

So: **records and metadata in SQLite, payloads as files named by their hash**, in
a sharded directory. Git objects, the Nix store, the IPFS blockstore and Docker
layers are all this.

Integrity follows from the naming: the filename *is* the hash, so an entry is
immutable, self-verifying and idempotent, and merging two stores is the union of
two directories. Writes are temp-file-then-rename.

One rule taken from NEARBYTES (`log-api-v1.md:13`): **the store is the sole
authority of the hash namespace.** Callers do not compute a hash and hand it in;
the store hashes the bytes it persists and returns the address.

## 8. Eviction and collection

### Our store is a cache over a DAG, not an archive

Git cannot delete a reachable blob: it is not reproducible, and losing it is data
loss. Nearly everything here **is** reproducible — the DAG says how. If an
element is deleted while a stored sequence-of-handles still names it, the
reference dangles for an instant and resolution falls through to recompute.

**For a recomputable value, a dangling reference costs time and can never
produce a wrong value.** The exception is the next subsection, and it is the only
one: where recomputation would return something different, deletion is not a cost
but a loss, and those entries must never be deletable.

This is not an assumption; the store already guarantees it. `record_lineage`
(`node_table.py:274`) writes each node's kind, operator and arguments into the
`node` table, so a hash is resolvable either from its bytes or from **its
recipe**. `storage.py:48` states the intent in as many words -- *"regenerable
from lineage, so an evicted entry only ever costs a recompute"* -- and an evicted
row survives as `status='evicted'` carrying that lineage (`:730`).

It matters more with handles than without. A stored sequence names element
hashes, and a later run may hold that sequence without ever having built the
elements. Without a persisted recipe such a reference would be unrecoverable
rather than merely cold, and the argument of this section would collapse into
"we need reachability GC after all". With lineage, a hash is always a question
the store can answer.

This is Nix's arrangement rather than Git's: Nix stores the derivation beside the
output and can therefore rebuild what it collects, where Git can only refuse to
collect. We already have the derivation.

So we do not need mark-and-sweep by reachability, which exists to protect the
irreproducible. We need **size- and cost-bounded eviction**, which is the policy
the RAM tier already implements (`node_table.py:391`, cost-aware: expensive
results kept over cheap ones), extended to disk.

Append-only is not an option — the disk fills.

### The exception, and it must be a root

A value that is **not a function of its arguments** cannot be recomputed: it
would come back different. `nnunet.train` is the case in hand — random init, data
order, GPU reduction order. Deleting it does not cost time, it destroys an
experiment.

These are few, and they must be marked as roots that disk eviction never touches.
This makes explicit something the project already relies on informally.

### What gets superseded

`engine/core.py` special-cases sequence-shaped values in about ten places. They
are one family — *this value is a list, so treat it differently* — and with a
sequence value that is a small list of hashes the family has nothing left to say:

| site | today | after |
|---|---|---|
| `_SEQUENCE_OPERATORS` (:64) | names the family | needed only by the expander |
| `_is_critical` (:1209) | sequences always critical, to keep them out of the reuse cut | a list of hashes is tiny; persist it always, trivially |
| `_recomputable` (:900) | sequences excluded — built by expansion, no kernel to re-run | `default.sequence` has a kernel and becomes ordinarily recomputable; `for_loop`/`map` stay expansion-built |
| `complete` (:1020-1022) | `complete_item` persists each element under a derived key | redundant: elements are nodes with their own ids and their own records |
| `hash_sequence_item` | derived per-element addressing | redundant, same reason |

A full re-reading of the reclaim passes (`_reclaim_memory`, `core.py:724-880`)
against this change is part of the work, not an afterthought: PASS 2's
"force a write so PASS 1 can free it" exists partly because large sequences could
not be written at all. Section 12 records what that re-reading concludes.

## 9. Operator inspection

Every operator is inspected for whether it could be lazy. Nothing is assumed from
a name. The census to cover: `default` (29 modules), `arrays`, `geom`, `nnunet`,
`simpleitk`, `strings`, `test`, `vox1`, plus the dynamically registered
namespaces.

Expected candidates, to be confirmed rather than asserted: `sequence`, `index`,
`subsequence`, `slice`, `filter`, `map`, `for_loop`, `print`, and `nnunet.train`.
Results go in section 11.

## 10. Lazy `if`

There is no conditional in the language. `vox1/compat.imgql:68` defines one
arithmetically:

```
let ifB(cond,th,el) = or(and(th,bconstant(cond)),and(el,not(bconstant(cond))))
```

It computes **both branches** and masks them, because there is no way not to.

A lazy `if` receives three handles, resolves nothing, and rewrites to the taken
branch. It is the smallest honest proof that laziness is general rather than a
sequence special case, which is why it is in this pass.

It is also an observable behaviour change: fewer nodes are computed, so less
enters the store, so a warm re-run prunes differently. Intended, and recorded
here so it is not discovered later.

## 11. Operator census

*(filled during implementation)*

## 12. What the eviction re-reading concluded

`_reclaim_memory` (`core.py:724-885`) exists **because of the problem this design
removes.** Its own docstring names it:

> THE VALVE FOR THE SEQUENCE-ASSEMBLY FLOOR: refcounting alone holds a loop
> body's value resident from completion until its *last* consumer runs -- for a
> wide loop whose sequence node needs every body, that means every completed body
> stays resident for the whole unroll. Peak RSS then tracks element count x body
> size, and no admission policy can fix this.

and then states the limit of the valve:

> Without a disk backend there is no reload path, so this is a no-op -- the floor
> is then a genuine, irreducible requirement of materializing every element
> before combining them.

That last sentence is the assumption this design retires. **A sequence node that
gathers hashes never materializes its elements**, so there is no floor to valve.
The requirement was never irreducible; it followed from the value of a sequence
being a list of values.

It also explains why the valve did nothing in #51. Every exit in PASS 2 needs the
value to be durable or cheaply recomputable:

| PASS 2 exit | condition | in #51 |
|---|---|---|
| evict, already persisted | `table.persisted(nid)` | never: a sequence of images is unserializable (§7) |
| drop and recompute | `compute_ms < sacrifice_ms and _recomputable(nid)` | never: sequences are excluded from `_recomputable` (`:900`) |
| force a write | `table.spill(nid)` | never: the encoder raises on the first image |

Three exits, all closed, on the one value that mattered -- 51.4 GB with every
route out blocked. The engine was not failing to apply its policy; it had no
applicable policy. `evicted_early` at 4440 and 1513 governor trims are the record
of it trying.

### What survives, and what improves

**Superseded.** The sequence-assembly floor, and with it the valve's reason for
existing. The special-case family in section 8's table goes with it.

**Survives unchanged, and is still needed.** PASS 0 (ownerless garbage) has
nothing to do with sequences -- it collects speculation that no consumer will
ever read, and the comment records 23.3 GB once held as "cache". PASS 1
(durable-and-pending) and the rest of PASS 2 remain the general answer to memory
pressure over ordinary values.

**Improves without being redesigned.** PASS 2's three exits start working on
element values where they could not work on the gathered list: an element is an
ordinary image, so it is persistable, spillable, and -- being an ordinary
primitive result rather than an expansion product -- recomputable. The policy
does not change; it acquires values it can act on.

**Left alone deliberately.** `sacrifice_ms`, the pressure ramp, `_EVICT_SWEEP`,
the dispatch-pin deferral and the two-queue split are each documented against a
specific measured failure. Nothing in this design bears on them, and touching
them in the same pass would make a regression impossible to attribute.

## 13. Non-goals

- The `--no-engine` lazy strategy is **not** carried across. It has its own
  sequence handling and its own demand model, and maintaining two value
  representations is worse than declaring it unsupported on handle values.
  Checked rather than assumed: no test in `tests/` references it, so this costs
  no coverage.
- Reachability GC is not implemented, and section 8 argues it is not needed.
- No change to the scheduler's priority, admission or governor policies beyond
  what section 8 supersedes.

## 14. Testing

- The eager path is unchanged: the existing suite is the test, and it must stay
  at its current pass/fail line.
- **Zero cost is a claim, so it is measured**: the adapter on a value containing
  no handles must not show up against `incoming` on a benchmark that spends its
  time in kernels. A claim of "literally what happens today" that nobody timed is
  the kind of assertion this project has been wrong about before.
- Handle identity: a handle round-trips through the store and means the same
  node in a fresh process.
- Liveness: a value containing a handle keeps the referenced node alive; the
  referenced node is released when that value is dropped.
- The #51 case, reduced: a sequence of N large values consumed by a lazy
  operator has peak residency of one element, not N. This is the acceptance
  test — it fails on `main` and must pass here.
- Lazy `if`: the untaken branch's node is never computed.
- Persistence: a sequence of images survives a store round-trip losslessly,
  which is impossible today.
- Dangling: deleting a store entry that a persisted sequence names produces a
  recompute, never a wrong value.

## 15. Specification: one decision point, and dependencies that are real

Written before the code, because the defect this fixes is precisely a decision
taken in one place and consulted in another.

### 15.1 The decision point

`engine/evaluation.py`, and nothing else, answers how a node is evaluated:

```python
@dataclass(frozen=True, slots=True)
class Modes:
    lazy: bool = False      # arguments arrive as handles
    shallow: bool = False   # arguments arrive as values, contents untouched
    rewrite: bool = False   # evaluating this GROWS THE GRAPH

def modes_of(registry, operator: str) -> Modes:   # memoized per operator
```

`lazy` and `shallow` are exclusive; `rewrite` is orthogonal to both.

**Every road that evaluates or classifies a node asks `modes_of`.** The roads,
exhaustively -- this list is the point of the section:

| caller | today | after |
|---|---|---|
| `executor._compute_node` | its own `lazy` lookup | `modes_of` |
| `core._rematerialize` | nothing: calls the kernel | `modes_of(...).rewrite` -> §15.2 |
| `core._recomputable` | `operator not in _SEQUENCE_OPERATORS and not can_expand` | `not modes_of(...).rewrite` |
| `core._is_critical` | `operator in _SEQUENCE_OPERATORS` | `modes_of(...).rewrite` or the fan-out rule |
| `core.complete` (`complete_item`) | `operator in _SEQUENCE_OPERATORS` | `spec.kind == "sequence"`, which already exists |
| `expander.can_expand` | `operator in _EXPANDABLE` | `modes_of(...).rewrite` |

`_EXPANDABLE` and `_SEQUENCE_OPERATORS` are deleted. The second answered three
unrelated questions at once, which is how one of them got lost.

**Invariant.** No module outside `evaluation.py` and the primitive specs names an
operator in a literal. A test enforces it by reading the sources: this is the
kind of rule that decays silently, so it is checked mechanically rather than
remembered.

**Extending it.** A new graph-growing operator writes `rewrite=True` in its spec
and is routed correctly everywhere, because there is nowhere else to tell.

### 15.2 The miss path for a rewrite node

`_rematerialize(nid)` on a node with `rewrite`:

1. if `nid` has an `_alias`, follow it -- the spliced sequence carries the value;
2. otherwise the node must be **re-expanded, never computed**. `_rematerialize`
   is synchronous and its callers expect a value, so it raises a typed
   `NeedsExpansion(nid)` and the scheduler re-registers the node.

Calling the kernel is removed as an option, not guarded against. `for_loop` has a
kernel and it belongs to the strict runtime, which reconstructs a closure the
engine never builds; reaching it produces `requires closure argument at key
'closure' or '1'`, which is the regression this closes.

### 15.3 Dependencies discovered from handles

Today an eager operator whose argument contains handles resolves them by calling
`_rematerialize` from a worker thread. When the value is resident that is a dict
lookup. When it is not, it is a reload -- or a **recompute**, which can drag in
subtrees -- that the scheduler never planned. That is an evaluation happening
outside the graph, and the graph is supposed to be the only witness.

**Rule.** When node `D` completes with a value naming `{e...}` by handle, every
eager dependent `A` of `D` gains `A -> e` as real edges before `A` can fire.

Four properties, each load-bearing:

- **Same mechanism as expansion.** The edges are registered through `admission`,
  the path loop expansion already uses. No second dynamic-edge machinery, and --
  the point -- the window that stops a 369-case unroll from exploding is the same
  window that bounds this.
- **Same walk.** Discovery reuses the value walk `graph.hold_handles` already
  performs at completion. No new traversal, no new cost.
- **Before the fire.** `on_complete(D)` releases inputs and fires dependents.
  Discovery must run BEFORE the fire, or `A` dispatches with edges that were
  about to exist.
- **One level per dispatch, and this is the anti-explosion property.** A
  handle's own value may name further handles. Those are discovered when THAT
  node's value is needed, not now. Discovery is never transitive in one step, so
  the work added at any completion is bounded by what that one value names.

An edge added this way cannot make a cycle: a handle names a node that already
completed, so it points backwards in time.

**What this does not do.** An eager operator that genuinely wants N values still
gets N values -- for `nnunet.train` that is the 51.4 GB, honestly. The cure is to
make it a rewriter (§4), not to hide the cost. What changes is that the N are
individual values the governor can spill, materialized by the scheduler under its
own bound, instead of one gathered list it could neither evict nor write.

### 15.4 What stops being possible

- A kernel, or the code adapting arguments for one, triggering a recompute the
  scheduler did not plan.
- Two evaluation roads disagreeing about a node, because there is one answer.
- A new graph-growing operator working in dispatch and failing on a miss.

### 15.5 Tests

- **No literals**: no module outside `evaluation.py` names `for_loop`, `map`,
  `sequence` or `filter` in a string.
- **The regression**: a rewrite node whose value is gone is re-expanded; its
  kernel is never called. Fails on this branch today.
- **Real edges**: an eager consumer of a handle container has the referenced
  nodes among its dependencies before it dispatches.
- **One level**: a container of containers adds only the first level's nodes at
  the outer completion.
- **No out-of-band work**: with the resolver instrumented, no eager dispatch
  triggers a recompute.
- **Extensibility**: a test-only primitive declaring `rewrite=True` is routed
  correctly by dispatch and by the miss path, without touching the engine.
