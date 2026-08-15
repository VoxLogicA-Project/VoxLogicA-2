// Which viewer shows a card.
//
// A card does not draw itself: it says what it is, and a viewer is chosen for
// it. The choice is made from the card's `kind` and -- once a result card has a
// result -- from the *type* of that result, which is why this is a function of
// both rather than a field on the card. A card bound to a number and a card
// bound to an image are the same kind of card; they are not the same view.
//
// There is one viewer today, and it is provisional: a plain text editor. It
// exists to be the seed. What replaces and joins it -- an image viewer, a table,
// a chart, a graph of the DAG -- arrives here as another entry in one table
// rather than as another branch in a component.
//
// See doc/dev/ui-workspace.md.

import TextEditor from "./TextEditor.svelte";

/** What a result's type is called, when we know it. Empty for now: results are
 * not wired up, and inventing type names before there are values to have them
 * would be inventing the wrong ones. */
const BY_RESULT_TYPE = {};

const BY_KIND = {
  code: { component: TextEditor, mono: true, editable: true },
  note: { component: TextEditor, mono: false, editable: true },
  result: { component: TextEditor, mono: true, editable: false },
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
