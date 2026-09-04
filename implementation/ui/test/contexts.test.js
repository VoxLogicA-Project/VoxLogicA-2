// The WebGL budget, tested where it can be: as a policy, with no browser.
//
// The failure this prevents cannot be tested in a browser at all -- a dropped
// context is silent, and the canvas that goes blank is the one nobody was
// looking at. So the policy is a plain object with a plain rule, and the rule
// is what is asserted.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Contexts } from "../src/lib/viewers/contexts.js";

const holder = (name, log) => ({ name, release: () => log.push(name) });

test("the pool never exceeds its capacity", () => {
  const pool = new Contexts(3);
  const log = [];
  for (const name of ["a", "b", "c", "d", "e"]) pool.acquire(holder(name, log));
  assert.equal(pool.size, 3);
});

test("the least recently wanted holder is the one told to let go", () => {
  const pool = new Contexts(2);
  const log = [];
  const a = holder("a", log);
  const b = holder("b", log);
  pool.acquire(a);
  pool.acquire(b);
  pool.acquire(holder("c", log));
  assert.deepEqual(log, ["a"]);
  assert.equal(pool.holds(a), false);
  assert.equal(pool.holds(b), true);
});

test("being wanted again moves a holder out of the firing line", () => {
  // The whole point of touching on hover: the card under the pointer must not
  // be the one whose picture is taken away.
  const pool = new Contexts(2);
  const log = [];
  const a = holder("a", log);
  pool.acquire(a);
  pool.acquire(holder("b", log));
  pool.touch(a);
  pool.acquire(holder("c", log));
  assert.deepEqual(log, ["b"]);
  assert.equal(pool.holds(a), true);
});

test("asking twice is not asking for two", () => {
  const pool = new Contexts(2);
  const log = [];
  const a = holder("a", log);
  pool.acquire(a);
  pool.acquire(a);
  assert.equal(pool.size, 1);
  assert.deepEqual(log, []);
});

test("a holder that throws on release does not take the pool with it", () => {
  // It is going away regardless; the one asking must still get its lease.
  const pool = new Contexts(1);
  const log = [];
  pool.acquire({
    name: "angry",
    release() {
      throw new Error("no");
    },
  });
  assert.equal(pool.acquire(holder("next", log)), true);
  assert.equal(pool.size, 1);
});

test("giving a lease back twice is ordinary, not an error", () => {
  // A component unmounting after it was already evicted is the common case.
  const pool = new Contexts(2);
  const log = [];
  const a = holder("a", log);
  pool.acquire(a);
  pool.drop(a);
  pool.drop(a);
  assert.equal(pool.size, 0);
});

test("the budget leaves room for whatever else is on the page", () => {
  // A browser gives about sixteen contexts in total, and a chart, a thumbnail
  // or a devtools panel is entitled to some of them.
  const pool = new Contexts();
  assert.ok(pool.capacity <= 8, `capacity ${pool.capacity} claims too much`);
});
