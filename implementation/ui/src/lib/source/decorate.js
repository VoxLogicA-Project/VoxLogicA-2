// The program, cut into spans that carry state.
//
// A pure function of text: no DOM, no store, no component. That is the point of
// having it at all -- the mirror in `SourceEditor` renders these spans, the
// document view renders the same spans, and both can be wrong in only one place.
// It is also the only part of the source surface that can be tested properly,
// because everything downstream of it is pixels.
//
// What it writes into the text is *state*: a name carries the state of the node
// it names, as weight and colour, rather than a badge beside it. A badge is a
// second thing to read; the name is already there and already the thing you are
// asking about. See doc/dev/ui-cards.md section 6.
//
// The tokeniser is deliberately shallow. It is not a parser and must never
// become one: the real one lives in Python, it is what the hashes come from, and
// a second understanding of .imgql in JavaScript would be wrong on the day
// somebody wrote a `for`. What this needs to know is narrower and stable --
// where the comments are, where the strings are, and which runs of characters
// are identifiers -- so that it never decorates a name that is really a word
// inside a comment.

/** @typedef {{text: string, kind: string, name?: string, state?: string}} Span */

const IDENTIFIER = /[A-Za-z_][A-Za-z0-9_]*/y;
const KEYWORDS = new Set([
  "let",
  "print",
  "save",
  "import",
  "for",
  "in",
  "fun",
  "filter",
  "fold",
  "true",
  "false",
]);

/**
 * Cut `text` into spans, giving every binding name the state of its node.
 *
 * Concatenating `span.text` in order reproduces `text` exactly. That is the
 * invariant the whole thing rests on: the mirror has to lay out identically to
 * the textarea it sits under, and a tokeniser that dropped or added a single
 * character would push every line below it out of register.
 *
 * @param {string} text the program, as written
 * @param {Record<string, string>} bindings name -> node hash, from the server
 * @param {(hash: string) => string} stateOf hash -> "done", "computing", …
 * @returns {Span[]}
 */
export function decorate(text, bindings = {}, stateOf = () => "unknown") {
  const spans = [];
  const source = text ?? "";
  let plain = "";

  const flush = () => {
    if (plain) {
      spans.push({ text: plain, kind: "plain" });
      plain = "";
    }
  };

  let i = 0;
  while (i < source.length) {
    const char = source[i];

    // A comment runs to the end of the line, and nothing inside it is a name.
    // This is the whole reason for tokenising rather than replacing words: a
    // comment mentioning `mask` must not light up when `mask` computes.
    if (char === "/" && source[i + 1] === "/") {
      flush();
      const end = source.indexOf("\n", i);
      const stop = end === -1 ? source.length : end;
      spans.push({ text: source.slice(i, stop), kind: "comment" });
      i = stop;
      continue;
    }

    // A string, with backslash escapes, ended by its quote or by the end of the
    // line -- an unterminated string is a file mid-edit, not a reason to
    // swallow the rest of the program.
    if (char === '"') {
      flush();
      let j = i + 1;
      while (j < source.length && source[j] !== '"' && source[j] !== "\n") {
        j += source[j] === "\\" ? 2 : 1;
      }
      const stop = Math.min(j + 1, source.length);
      spans.push({ text: source.slice(i, stop), kind: "string" });
      i = stop;
      continue;
    }

    IDENTIFIER.lastIndex = i;
    const match = IDENTIFIER.exec(source);
    if (match) {
      const word = match[0];
      if (KEYWORDS.has(word)) {
        flush();
        spans.push({ text: word, kind: "keyword" });
      } else if (Object.prototype.hasOwnProperty.call(bindings, word)) {
        flush();
        // The hash is what has a state; the name is only how it was reached.
        spans.push({
          text: word,
          kind: "binding",
          name: word,
          state: stateOf(bindings[word]) || "unknown",
        });
      } else {
        // A name this document does not bind: an operator, a primitive, or
        // something not written yet. Left plain rather than marked unknown --
        // `threshold` is not a node that failed to compute, it is not a node.
        plain += word;
      }
      i += word.length;
      continue;
    }

    plain += char;
    i += 1;
  }

  flush();
  return spans;
}

/** The class a span is rendered with. One place, so the CSS and the tokeniser
 * cannot drift apart in the way where everything still renders and nothing is
 * coloured. */
export function classOf(span) {
  if (span.kind === "binding") return `tok binding is-${span.state ?? "unknown"}`;
  return `tok ${span.kind}`;
}
