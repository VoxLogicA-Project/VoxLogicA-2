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
