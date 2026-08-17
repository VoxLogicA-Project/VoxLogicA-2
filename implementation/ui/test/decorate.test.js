// The first front-end unit tests in this tree, and they exist because
// `decorate` is the one piece of the source surface that *can* be tested:
// everything downstream of it is pixels, and pixels are checked by looking.
//
// Run by `node --test`, which ships with node and needs no dependency. The
// Python suite shells out to it (tests/unit/test_ui_decorate.py) so that
// `pytest -k ui` stays the one command that says whether the UI is broken.

import assert from "node:assert/strict";
import { test } from "node:test";

import { classOf, decorate } from "../src/lib/source/decorate.js";

const BINDINGS = { mask: "a".repeat(64), flair: "b".repeat(64) };
const STATES = { ["a".repeat(64)]: "done", ["b".repeat(64)]: "computing" };
const stateOf = (hash) => STATES[hash] ?? "unknown";

const rebuild = (spans) => spans.map((span) => span.text).join("");
const kindsOf = (text) =>
  decorate(text, BINDINGS, stateOf).map((span) => `${span.kind}:${span.text}`);

// -------------------------------------------------------------- the invariant

test("the spans put the text back together exactly", () => {
  // The mirror lays out under a textarea holding the same string. One character
  // gained or lost and every line below it is out of register -- which is the
  // failure that looks like the highlighting "drifting" further down the file.
  const samples = [
    "",
    "let a = 1\n",
    'let a = "x" // and a comment\n',
    "let mask = threshold(flair, 0.6)\n\n\n",
    "// only a comment",
    '"unterminated string',
    "let x = 1\r\nlet y = 2\r\n",
    "  \t indented\n",
    "ünïcode = 1\n",
  ];
  for (const text of samples) {
    assert.equal(rebuild(decorate(text, BINDINGS, stateOf)), text, JSON.stringify(text));
  }
});

test("a text with no bindings is still reproduced", () => {
  const text = "let whatever = 1\n";
  assert.equal(rebuild(decorate(text)), text);
});

// ------------------------------------------------------------------- meaning

test("a bound name carries the state of its node", () => {
  const spans = decorate("let mask = flair\n", BINDINGS, stateOf);
  const bound = spans.filter((span) => span.kind === "binding");
  assert.deepEqual(
    bound.map((span) => [span.name, span.state]),
    [
      ["mask", "done"],
      ["flair", "computing"],
    ],
  );
});

test("a name the document does not bind is left plain", () => {
  // `threshold` is not a node that failed to compute; it is not a node. Marking
  // it unknown would put a state on every operator in the language.
  const spans = decorate("let mask = threshold(flair)\n", BINDINGS, stateOf);
  assert.equal(
    spans.some((span) => span.kind === "binding" && span.name === "threshold"),
    false,
  );
});

test("a bound name with no state reads as unknown rather than as nothing", () => {
  const spans = decorate("mask\n", BINDINGS, () => "");
  assert.equal(spans[0].state, "unknown");
});

test("keywords are keywords", () => {
  assert.deepEqual(kindsOf("let x = 1\n").slice(0, 1), ["keyword:let"]);
  assert.ok(kindsOf('print "a" mask\n').includes("keyword:print"));
});

// ------------------------------------------- what the tokeniser exists to stop

test("a name inside a comment is not decorated", () => {
  // The whole reason this tokenises instead of replacing words: a comment
  // mentioning `mask` must not light up when `mask` computes.
  const spans = decorate("// mask is done\nlet a = 1\n", BINDINGS, stateOf);
  assert.equal(spans[0].kind, "comment");
  assert.equal(
    spans.some((span) => span.kind === "binding"),
    false,
  );
});

test("a name inside a string is not decorated", () => {
  const spans = decorate('print "mask" flair\n', BINDINGS, stateOf);
  const bound = spans.filter((span) => span.kind === "binding").map((span) => span.name);
  assert.deepEqual(bound, ["flair"]);
});

test("an escaped quote does not end a string", () => {
  const text = 'let a = "he said \\"mask\\" then left"\n';
  const spans = decorate(text, BINDINGS, stateOf);
  assert.equal(rebuild(spans), text);
  assert.equal(
    spans.some((span) => span.kind === "binding"),
    false,
  );
});

test("an unterminated string stops at the line, not at the file", () => {
  // A file mid-edit, which is the common case -- not a reason to swallow every
  // line below it.
  const spans = decorate('let a = "oops\nlet mask = 1\n', BINDINGS, stateOf);
  assert.ok(spans.some((span) => span.kind === "binding" && span.name === "mask"));
});

test("a name that merely starts with a bound name is not that name", () => {
  const spans = decorate("let masked = 1\n", BINDINGS, stateOf);
  assert.equal(
    spans.some((span) => span.kind === "binding"),
    false,
  );
});

// -------------------------------------------------------------------- classes

test("the class carries the state, in one place", () => {
  assert.equal(classOf({ kind: "binding", state: "computing" }), "tok binding is-computing");
  assert.equal(classOf({ kind: "binding" }), "tok binding is-unknown");
  assert.equal(classOf({ kind: "comment" }), "tok comment");
});

// ---------------------------------------------- the layout the CSS depends on

test("every span carries text, so the mirror is never empty when the source is not", () => {
  // The lesson from a real failure: the editor collapsed to zero height with
  // the text present in the DOM, and the alignment check passed because it was
  // comparing two empty rectangles. A browser test that only compares the two
  // layers to each other can be satisfied by both being nothing -- so the thing
  // to assert is that there is something to lay out at all.
  const text = "let mask = flair\n";
  const spans = decorate(text, BINDINGS, stateOf);
  assert.ok(spans.length > 0);
  assert.ok(spans.some((span) => span.text.trim().length > 0));
  assert.equal(rebuild(spans).length, text.length);
});
