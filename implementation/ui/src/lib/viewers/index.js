// Which viewer shows a card.
//
// A card does not draw itself: it says what it is, and a viewer is chosen for
// it. The choice is made from the card's `kind` and -- once a result card has a
// result -- from the *type* of that result, which is why this is a function of
// both rather than a field on the card. A card bound to a number and a card
// bound to an image are the same kind of card; they are not the same view.
//
// The table below is the extension point. An image viewer, a table, a chart, a
// graph of the DAG: each arrives as another row here, keyed by the type name the
// server puts on a result, rather than as another branch inside a component.
//
// See doc/dev/ui-workspace.md.

import TextEditor from "./TextEditor.svelte";
import ResultState from "./ResultState.svelte";

/** What a result's type is called, and who draws it.
 *
 * Only the types the server actually emits belong here. `ResultState` is what
 * every result falls back to and it is not a placeholder: for an image or a
 * volume, "done, 240×240×155 float32" *is* the useful view, and a richer one is
 * an addition to it rather than a correction of it. */
const BY_RESULT_TYPE = {
  number: { component: ResultState, mono: true, editable: false, result: true },
  string: { component: ResultState, mono: false, editable: false, result: true },
  boolean: { component: ResultState, mono: true, editable: false, result: true },
};

const BY_KIND = {
  code: { component: TextEditor, mono: true, editable: true },
  note: { component: TextEditor, mono: false, editable: true },
  result: { component: ResultState, mono: true, editable: false, result: true },
};

const FALLBACK = { component: TextEditor, mono: true, editable: true };

/**
 * The viewer for a card, and how it should be run.
 *
 * @param {{kind?: string}} card
 * @param {{type?: string}} [result] the value the card is showing, if any
 */
export function viewerFor(card, result = undefined) {
  if (result?.type && BY_RESULT_TYPE[result.type]) return BY_RESULT_TYPE[result.type];
  return BY_KIND[card?.kind] ?? FALLBACK;
}
