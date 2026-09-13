# Reusing a warm store without replaying the expansion

**Status:** design, not implemented. Written 2026-09-13 after a warm run redid
nine million nodes of work it already had on disk.

## 1. The problem, measured

A runtime loop's element ids exist only after the loop is *expanded*: the body is
reduced once per element, in Python, and the resulting specs are interned. Values
are content-addressed, so a warm run can skip any *kernel* whose value is on
disk — but it can only ask "is this on disk?" once it knows the id, and knowing
the id is what the expansion is for.

So a warm run skips the kernels and redoes all of the discovery. Measured on the
sixty-case oracle sweep:

| | |
|---|---:|
| static plan | **107,728** nodes, built in 4.5 s |
| after runtime expansion | **9,022,374** nodes |
| specs in the store's `node` table | **107,728** — the static plan, and nothing else |
| result rows in the same store | 3,252,459 |

The store knows about three million results and the shape of a hundred thousand
nodes. **The nine million ids that name those results are not stored anywhere**,
so every warm run re-derives them from scratch, which is hours of pure Python
before a single kernel is skipped.

## 2. Why the obvious fix was withdrawn, and the invariant that was missing

The engine used to persist a `for_loop` node's value as a sequence of handles.
`_EXPANDED_OPERATORS` in `engine/core.py` now forbids it, and the comment there
records why, from the store that broke:

> one `default.for_loop` row, `status='materialized'`, `vox_type` 'sequence', no
> payload file, and a 19,404-byte `sequence-json-v1` payload holding 225
> handles — of which **0 had a row in the `node` (spec) table** and only 203 had
> a materialized value anywhere.

A warm run saw the loop was "available", pruned its whole subtree, and the
expansion that alone could intern those 225 specs never ran. The first deep
resolve of a missing element raised `NeedsExpansion` on a pool thread,
deterministically, 37 seconds into every run against that store.

**The defect was not "persisting the expansion". It was persisting references
without their referents.** One invariant would have made it impossible:

> **A stored node may name only nodes whose specs are also stored.**

This is the *closure* property: Nix calls it the closure of a derivation, IPFS
calls it a pinned DAG, and in both a store that violates it is corrupt by
definition rather than merely unlucky. Our store had no such rule, so a row could
name 225 children it did not have.

## 3. The design

Three parts. Nothing here needs a new storage engine: the `node` table already
exists with the right schema (`hash, kind, operator, args, kwargs, attrs_json`),
and `put_definition` already exists — it is simply never called for runtime nodes.

### 3.1 Persist the specs of expanded nodes

As each chunk of a loop body is reduced, write the interned specs to the `node`
table in the same transaction that records the chunk. Specs are small,
hash-consed and shared across cases, so the cost is bounded and mostly paid once:
at ~150–250 bytes a row, nine million specs are **1.4–2.3 GB against 1.6 TB of
payloads — about 0.1%**.

### 3.2 Key the expansion by the loop's content hash, and publish it last

Store, for each expanded loop `L`:

    H(L)  →  the spliced `default.sequence` node id

`default.sequence` is already the right shape and is deliberately *not* in
`_EXPANDED_OPERATORS`: **its args ARE its element ids, so its spec is
self-sufficient and rebuilding it needs no expansion.** The engine already has
the self-describing representation; what it lacks is the specs of the things that
representation names.

**Publication order is the whole safety argument.** The `H(L) → sequence` row
becomes visible only after every element spec it transitively names is durable.
Written in that order, the 2026-09-10 poisoning cannot recur: a visible row
always has its closure.

### 3.3 Warm path: intern, do not reduce

When `_schedule_subgraph` reaches a `for_loop` on a warm store:

1. look up `H(L)`; on a miss, expand exactly as today;
2. on a hit, read the sequence spec and intern its element specs directly from
   the `node` table — no body reduction, no Python, no chunk executor;
3. apply the ordinary `_available()` rule per element: every element whose value
   is on disk is pruned, the rest are scheduled.

A fully warm run then costs *one read per loop plus the interning*, not one
reduction per element.

### 3.4 What this also fixes

`node_table._references_are_answerable` currently accepts a stored container only
if every handle it names is "interned in this run", and §3 of the stability
handover records that this test is **time-dependent**: `self.nodes` grows as the
run proceeds, so the same question gets different answers depending on when it is
asked, which is the leading hypothesis for a stall that has resisted four fixes.
With the closure invariant the test becomes trivially true and time-independent —
a stored reference always has a stored referent. **The fix removes the
interaction rather than reasoning about it.**

## 4. Why this is the standard answer, not an invention

The distinction being drawn is between caching a computation's *result* and
caching its *analysis*. Our engine caches the first and repeats the second.

- **Bazel / Buck2** separate analysis from execution and cache both; Skyframe
  memoises analysis nodes keyed by content, which is why a warm Bazel build does
  not re-analyse the graph it already understands.
- **Nix and Guix** content-address *derivations* — the description of how to
  build — not only outputs, and enforce closure: a store path's references are
  part of what is stored.
- **Unison** identifies every definition by the hash of its syntax tree, so a
  codebase is a content-addressed store of ASTs and nothing is re-parsed or
  re-analysed.
- **Mokhov, Mitchell and Peyton Jones, "Build systems à la carte" (ICFP 2018)**
  give the vocabulary: what we need is a *deep constructive trace* — a record of
  the dependency discovery keyed by the content of the task — rather than the
  shallow verifying trace we keep today.
- **IPLD / Merkle DAGs** are where the closure discipline is a definition rather
  than a convention: a block names children by hash, and a store that cannot
  produce a named child is incomplete, detectably.
- **Incremental computation** — Adapton, Salsa, Jane Street's Incremental —
  memoise the *structure* of a demand-driven computation, which is exactly the
  structure our expansion rebuilds every run.

The property that makes all of this safe in our setting is already ours:
expansion is deterministic and hash-consed, and the engine's own documentation
states it — "chunk boundaries and admission order cannot change node identity …
so incremental expansion yields byte-identical ids to monolithic expansion". A
deterministic function of a content-addressed input is exactly what may be
memoised by that input's hash.

## 5. Cost, and what would falsify the design

| | |
|---|---|
| storage | 1.4–2.3 GB of specs per nine million nodes, ~0.1% of the payload tier |
| write cost | one small row per interned node, batched with the chunk that created it, on the persister thread that already exists |
| read cost, warm | one indexed lookup per loop, plus interning — replacing one Python body reduction per element |
| risk | a spec written without its closure; mitigated by publication order and by verifying the hash on read (a re-interned spec must hash to the id it was stored under, or the block is corrupt and is discarded) |
| invalidation | the key must include the reducer/format version, so changing the expander invalidates memoised expansions rather than silently reusing them |

**Falsifiable prediction.** On the sixty-case sweep against a store that already
holds one complete run, the second run should reach its first goal without
reducing a single loop body, and its node-registration count should be dominated
by interning rather than by expansion. If a warm run still spends hours before
its first kernel is skipped, the memoisation is not being hit and the key or the
determinism assumption is wrong.

## 6. Staged implementation

1. **A counter first, because reuse is currently unmeasurable.** Increment when
   `_schedule_subgraph` prunes a node because `_available` found it in the store.
   Without it, "the warm store bought X" is inference, and this document's
   prediction cannot be scored.
2. **Write specs** for runtime-expanded nodes (§3.1) and add the closure check on
   write. No read path yet: this alone is harmless and makes the next step
   measurable.
3. **Publish `H(L) → sequence`** after the closure (§3.2), still without reading
   it back.
4. **Read it** on the warm path (§3.3), behind a flag, with the equivalence
   harness of the stability handover §7 asserting that a warm run's goal values
   are *identical* to a cold run's — the values are content-addressed, so
   identity is the right test, not tolerance.

---

## 7. The second, larger problem: the store holds a sample, not a cut

§1–§6 are about *naming*: you cannot ask "is this on disk?" until expansion has
told you the id. There is a second problem underneath it, and it is the bigger
lever.

### What the engine already does right

`_schedule_subgraph` walks **down from the goal** and prunes at `_available(nid)`
— `completed or persisted`. So the rule "if a node's result is already
materialised, do not compute it *or anything below it*" is already implemented,
and correctly: an available node's inputs are never even visited.

### Why it does not fire

`persisted()` is a membership test against the materialised-id index, and an
**evicted row is a tombstone with `status='evicted'`** — not materialised, so not
available. Combine that with how the store chooses what to keep:

- **pressure shedding** drops whatever arrives while the four writer threads are
  busy — a decision made by arrival timing, not by worth;
- **GreedyDual-Size** then evicts by cost-per-byte.

Neither looks at where the node sits in the graph. The result is a store that is
a **sample** of the DAG: three million rows with holes everywhere, 856 K
materialised ids out of 2.64 M nodes in one measured run — about 32%, chosen
essentially at random.

**And a hole in the boundary forces the whole interior below it to be recomputed.**
If a consumer's value was shed, the walk descends into its inputs; those inputs
were evicted as *dead* — tombstoned, because at the time nothing needed them any
more — so they are not available either, and the walk continues to the leaves.
Everything below one missing value is recomputed, all of it work that was
performed once and correctly discarded.

### The user's rule, corrected

*"A value that was evicted and has a tombstone is very likely unneeded — expand
but skip."* The instinct is right and the mechanism needs one correction: you
**cannot** skip a tombstoned node when something genuinely asks for it, because
the value is gone and only recomputation can produce it. What you can do is
arrange that **nothing asks** — and that is a property of what you keep, not of
what you discarded.

### The design: store a cut, not a sample

Choose a **cut set**: a frontier that separates the goals from the leaves, and
guarantee *that* is complete. Then a warm run prunes at the cut, never descends
below it, and every tombstone underneath is genuinely never asked for — the
user's "expand but skip", obtained by construction instead of by guessing.

The cheapest useful cut on this workload is already obvious from the program's
shape: **the per-case result of each loop body**. Sixty of them, one per case,
plus the goal values. Persist those unconditionally — never sheddable, never
evictable — and a warm run prunes sixty subtrees at their roots and does nothing
else. The interior, which is 99.99% of the nine million nodes, is never named,
never expanded below the cut, and never recomputed.

Concretely that is a change to the persist gate, not a new mechanism:

1. mark a node **critical** when it is a loop body's root or a goal's direct
   input (the engine already has a `critical` concept in the persist gate:
   `critical or compute_ms >= persist_min_compute_ms`);
2. critical values are exempt from pressure shedding and from eviction while any
   future run might want them — the same exemption `protected` already gives
   goals within a run;
3. everything else stays exactly as it is: shed on pressure, evict by
   GreedyDual-Size. Interior values are *supposed* to be disposable.

### Why this is the standard answer

Choosing which intermediate values to keep so that the rest can be cheaply
recomputed is **checkpoint selection**, and it is a solved problem in two
literatures:

- **Rematerialisation / gradient checkpointing** — Chen, Xu, Zhang and Guestrin,
  "Training deep nets with sublinear memory cost" (2016): keep a cut of the
  activation graph, recompute the segments between checkpoints, and the memory
  falls to O(√n) for a bounded recompute cost.
- **Checkpointing for adjoint computation** — Griewank and Walther's `revolve`
  (ACM TOMS, 2000): the optimal placement of checkpoints along a computation to
  minimise recomputation under a storage bound.
- And the **red-blue pebble game** (Hong and Kung, 1981) again, which is the
  formal statement of the whole question: which values to hold in fast storage so
  that the traffic to slow storage is minimised.

In all of them the checkpoints are **chosen**. Ours are whichever writes won a
queue race. That is the defect, stated in one line: **the engine checkpoints at
random, and a random cut of a DAG has holes, and a hole costs its entire
subtree.**

### Which of the two to do first

§7 is cheaper and pays more. A complete cut at loop-body roots makes a warm run
prune at sixty nodes, which is most of the benefit of §3 without needing the
expansion memoisation at all — the engine still re-expands, but it expands into
subtrees it immediately prunes. §3 then removes the remaining cost, which is the
expansion itself.

---

## 8. Store-guided scheduling: walk the sparse DAG in the store, and expand only into what must be computed

§3 removes the cost of *naming* and §7 removes the cost of *holes*. This section
puts them together into the scheduling discipline they enable, which is the one
the engine should have had from the start: **do not build the plan and then prune
it — consult the store first and build only what is missing.**

### 8.1 What the store actually is

A store is a **sparse clone of the DAG**: a spec table that knows the shape of
some nodes, a result table that holds values for some of them, and tombstones
recording that others were produced once and discarded. Today the engine treats
it as a lookup table consulted *after* the plan exists. It is better understood
as a partial answer to the question the plan is asking, and the scheduler should
start from it.

### 8.2 The algorithm

Goal-directed, demand-driven, with an explicit stack. Written out with the
reasoning attached, because every clause here is load-bearing:

```python
def must_compute(goals):
    """The nodes this run has to produce, and nothing else.

    STACK, NOT QUEUE, and it is not a detail. A depth-first walk finishes a
    node's subtree before opening a sibling, so the number of values that must
    be simultaneously live is proportional to the DEPTH of the DAG. Breadth-first
    makes it proportional to the WIDTH -- which on a sixty-case sweep is sixty
    whole cases at once, and is precisely the shape that produced 47,141 values
    pinned by unrun consumers against room for 5,000 (stability handover, 28.1).
    For trees the depth-first order is optimal (Sethi and Ullman 1970); for DAGs
    the problem is NP-complete (Sethi 1975), so depth-first is a heuristic -- but
    it is the heuristic every serious scheduler uses, and the measured
    alternative is thrashing.
    """
    work = list(goals)            # the stack; goals are the only roots
    must = set()                  # what this run will compute

    while work:
        nid = work.pop()          # LIFO -- see the docstring

        if nid in must:
            continue              # hash-consing means a node is reached many times

        # THE CUT. This is the single availability rule, the same one every
        # registration path already uses: a node whose value is in the store
        # needs neither computing NOR exploring, and its whole subtree is
        # therefore invisible to this walk. With the critical-cut policy of 7,
        # this is where a warm run stops -- sixty loop-body roots, and nothing
        # underneath them is ever named.
        if available(nid):
            continue

        # A TOMBSTONE IS NOT AVAILABILITY. `persisted()` answers from the
        # materialised-id index, and an evicted row is `status='evicted'` with
        # no payload. The value is gone; only recomputation can produce it. The
        # tombstone is still worth reading -- it says this node was computed
        # once and later became garbage, which is evidence that the cut ABOVE
        # it is the right place to keep values -- but it can never license
        # skipping the node when the walk has genuinely reached it.
        must.add(nid)

        for dep in dependencies_of(nid):
            work.append(dep)


def dependencies_of(nid):
    """A node's inputs, WITHOUT running its kernel and without reducing a loop.

    Three cases, and only the third is new:

    1. An ordinary node: its spec's args and kwargs. Known statically, free.

    2. An expanded node (`for_loop`/`map`/`filter`) WITH a memoised expansion
       (3.2): read the spliced `default.sequence` id, intern the element specs
       from the `node` table, and return them. No body reduction happens. This
       is the step that makes the whole design possible -- without it, finding
       out whether element i is needed costs exactly the reduction we are trying
       to avoid.

    3. An expanded node with NO memo (a cold run, or a changed reducer version):
       expand it as the engine does today, write the specs and then the memo
       (3.1, 3.2), and return the elements. The cost is paid once, ever, per
       loop shape.

    THE WALK GROWS AS IT LEARNS, which is why it is a worklist and not a
    recursion over a plan that must exist first. Case 3 pushes nodes that did
    not exist when the walk started. That is normal and is the reason the
    algorithm is written as an explicit stack rather than as a traversal of a
    finished graph -- and, separately, the reason it must never be written as
    recursion: the depth here is the depth of the DATA, and this engine builds
    tens of millions of nodes in one process (see AGENTS.md, "No recursion").
    """
```

### 8.3 What changes, concretely

| | today | with this |
|---|---|---|
| when the plan is built | eagerly, whole cone from each goal | only along nodes that must be computed |
| when a loop is expanded | always, in full, then pruned per element | only when the walk must descend through it, and on a warm store not at all |
| what is registered | every reachable node | only `must` |
| what is pinned | every registered consumer's inputs — 154,705 measured | the inputs of `must` only |
| node count known | after hours of expansion | before the first kernel, from the walk |

The last row is a free result worth naming: the walk produces an **exact** node
count before any computation, which retires the plan-size estimator and the ETA
that read "12 minutes" for seven hours.

### 8.4 What this does NOT need, and it is worth stating

**Partial expansion of a loop is not required.** It looks necessary — if 59 of
60 cases are complete, why reduce all 60? — but it is not, and trying would be a
mistake. A `default.sequence` node's args *are* its element ids, so its identity
depends on all of them: you cannot name the sequence without naming every
element. What you can avoid is *computing* them, and the availability check
already does that.

With 3's memo, naming all sixty costs one indexed read. Without it, naming costs
sixty body reductions. So the correct order of work is 3 first as the *enabler*,
7 as the *payoff*, and this section as the discipline that joins them — and
partial expansion, which is the tempting third idea, is unnecessary in every case
where the memo exists.

### 8.5 Where it plugs in

`_schedule_subgraph` in `engine/core.py` already is this walk, minus the two
things that matter: it uses `frontier.pop()` (so it is already depth-first —
that part is right), and it prunes at `_available` (so the cut is already
there). What it lacks is case 2 of `dependencies_of` — a loop it meets is
scheduled and expanded in full by `admission`, rather than being *read*.

So this is not a new scheduler. It is `_schedule_subgraph` plus a memo, and the
change is: **when the walk meets an expanded operator, ask the store for its
elements before asking the expander to produce them.**

### 8.6 Invariants, stated so they can be tested

1. **Closure.** A memoised expansion is visible only when every spec it
   transitively names is durable (3.2). Violating this is exactly the
   2026-09-10 poisoning.
2. **Determinism.** Re-interning a spec read from the store must hash to the id
   it was stored under. Checked on read; a mismatch discards the block rather
   than trusting it. This is the Merkle property and it is what makes a shared
   or copied store safe.
3. **Version.** The memo key includes the reducer/format version, so changing
   the expander invalidates memoised expansions instead of silently reusing
   them.
4. **No recursion.** The walk is a `while` over a list, at every depth, forever.
5. **Availability is one rule.** `available()` is the single predicate, used by
   the walk, by admission and by the loop-body check — the engine has already
   been bitten once by a second, hand-rolled copy that disagreed about
   persisted-but-pruned bodies and could deadlock a partially warm cache.

### 8.7 The test that must fail first

Two runs of the same program against one store, the second with every value from
the first still present:

- **the second run reduces zero loop bodies** (a counter on the expander, which
  does not exist yet and is step 1 of the staging in §6);
- **the second run registers only the goals' immediate cone**, not millions of
  nodes;
- **the goal values are identical**, not merely close — they are
  content-addressed, so identity is the right test.

On today's engine that test fails on all three counts, and the failure is the
subject of this document.

---

## 9. Why the read path is correct by construction, not by measurement

§3 and §8 describe what to build. This section is the correctness argument, and
it does not depend on the implementation being lucky. The claim is not "this
should work" but **"the engine cannot tell the two paths apart, and if it ever
could, it takes the old one."**

### 9.1 What the engine consumes

Expansion produces exactly one thing the rest of the engine can observe: a set of
interned pairs

    S(L) = { (h₁, s₁), … , (hₙ, sₙ) }        h_i = hash(s_i)

— element ids and their specs, placed in `table.nodes` — plus the spliced
`default.sequence` node whose args are those ids. Nothing downstream reads
anything else about how they came to exist. Scheduling, availability, admission
and eviction all act on ids and specs.

So to prove the read path safe it is enough to prove it produces the same S(L).

### 9.2 The three properties it rests on, each already true or checkable

1. **Expansion is deterministic.** Reducing body *i* is a pure function of the
   loop's spec and *i*, and hash-consing makes the resulting id a function of
   the spec alone. The engine already asserts this — "chunk boundaries and
   admission order cannot change node identity … incremental expansion yields
   byte-identical ids to monolithic expansion" — and it is the premise the
   current store already relies on every time a warm run's re-expansion hits a
   stored value.

2. **Identity is verifiable, not trusted.** A node's id *is* the hash of its
   spec. So a spec read back from the store can be **re-hashed and compared to
   the key it was stored under**. This is a check, not an assumption: a spec
   that does not hash to its own id is not the spec, and is discarded. This is
   the Merkle property, and it is what makes a store safe to copy between
   machines or share between users.

3. **Closure is maintained by publication order.** The memo row for L is written
   **after** every spec it names is durable, in a later transaction. A crash
   between the two leaves specs with no memo — harmless, since specs are
   `INSERT OR IGNORE` and the loop simply expands. The reverse order is what
   poisoned the store on 2026-09-10 and it is the one order the write path must
   never use.

### 9.3 The argument

**Claim.** For any loop L, the pairs the engine interns via the read path are
identical to the pairs it would intern by reducing L.

1. The memo for L is written by a run that reduced L, and stores exactly S(L)
   (§3.1, §3.2).
2. On read, each stored pair (h, s) is accepted only if `hash(s) == h`.
3. If every pair verifies, the accepted set is S(L) pair-for-pair, by (1) and
   (2).
4. If any pair fails to verify, or any named spec is missing, **the memo is
   rejected whole and the loop is reduced** — which is today's behaviour
   exactly.
5. Therefore the engine either interns S(L) from the store, or interns S(L) by
   reducing. In both cases it interns S(L). ∎

**Corollary.** The read path cannot change any result, any goal value, or any
scheduling decision. Its only observable effect is how long the interning took.

**The worst case is today.** Every failure mode — a corrupt block, a missing
spec, a changed reducer, an absent memo — lands on "reduce the loop", which is
the code path the engine runs now. There is no state in which the read path is
worse than not having it.

### 9.4 What this argument does NOT claim

It does not claim the memo will be *present* often enough to be worth having.
That is a question about coverage, not correctness, and it is the one that needs
measuring: how many loops a warm run finds memoised, and therefore how much of
the expansion disappears. **Correctness is argued; the speed-up is measured.**
Conflating those two is what made the earlier answer in this session sound
evasive, and the distinction is the whole content of this section.

### 9.5 The invariants, as tests

Each of the three properties above is a test that fails on a violating
implementation, not a comment:

1. **Determinism** — reduce the same loop twice in one process and assert the
   interned id sets are equal.
2. **Verification** — corrupt one stored spec's bytes, read the memo, and assert
   the loop is reduced rather than the bad spec interned.
3. **Closure** — delete one element spec from the store, read the memo, and
   assert the loop is reduced rather than a dangling reference interned.
4. **Equivalence** — expand a loop by reduction and by memo in the same test and
   assert the two sets of `(id, spec)` pairs are equal.
5. **End to end** — two runs against one store: the second reduces **zero** loop
   bodies and produces **identical** goal values.

(1)–(4) establish the argument above mechanically. (5) is the coverage
measurement, and it is the only one of the five that can fail for a reason that
is not a bug in this design.

---

## 10. A precondition §9 assumed and the store does not meet

§9 argues the read path is correct because a spec read back is **re-hashed and
compared to the key it was stored under**. That check is the whole safety
argument. It cannot currently pass, and the reason is worth recording before any
of §3 is built on top of it.

### The gap

`hash_node` feeds six things into the digest:

    kind, operator, args, kwargs, attrs, output_kind

The `node` table stores five:

    hash, kind, operator, args, kwargs, attrs_json

**`output_kind` is not stored.** A spec reconstructed from a row therefore
cannot reproduce its own id unless every node happens to share one
`output_kind`, so the verification step of §9.2(2) would reject every block and
the read path would fall back to reducing, every time — safe, and useless.

There is a second, subtler version of the same problem: `attrs` are stored as
`dumps_json(node.attrs)`, while the hash consumes `_normalize_value(node.attrs)`.
Anything the normaliser changes — a tuple becoming a list, a numeric type
narrowing — round-trips to a spec that is *equal enough to run* and *not equal
enough to hash the same*.

### The fix, and why it is the stronger one

Store the **canonical payload that the hash consumes**, not a decomposition of
it: `node_payload(node)` is exactly the dict `hash_node` reads, so a row holding
it re-hashes to its own id *by construction* rather than by the schema and the
hasher agreeing about six fields forever. The decomposed columns stay for
queryability; identity comes from the payload.

This is the same discipline as everything else here: make the invariant
structural instead of remembering to maintain it.

### What still needs deciding, and it is not mine to assume

Reconstructing a *runnable* `NodeSpec` needs `attrs` in the form the engine
executes with, and the canonical payload holds the **normalised** form. For the
element nodes an expansion produces — ordinary primitives over image values —
these coincide. For a `closure`, whose attrs carry an unreduced AST, they may
not.

So the open question is whether the memo needs to carry closures at all, or only
the primitive elements plus the spliced sequence. I believe only the latter, but
"I believe" is the wrong basis for the one check the correctness argument rests
on, and this session has four refuted fixes that were all somebody's belief.

**Next step is a measurement, not a patch**: take one expanded loop from run 8's
store, reconstruct each element spec from its row, re-hash it, and count how many
reproduce their id. That number decides whether §3 is a schema change or a
redesign, and it is one query against a store that already exists.
