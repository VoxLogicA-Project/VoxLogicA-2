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
import SourceEditor from "../source/SourceEditor.svelte";
import ResultState from "./ResultState.svelte";
import Volume from "./Volume.svelte";

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
  // The first viewer that draws the thing instead of describing it. A row,
  // exactly as the table promised -- no branch anywhere else changed.
  //
  // `needs` is the smallest this viewer is usable at, in cells. A volume in two
  // cells is a thumbnail of a crosshair; below that it is a black rectangle with
  // three letters on it. The board honours it as a floor and moves other cards
  // out of the way to grant it.
  image: {
    component: Volume,
    mono: false,
    editable: false,
    result: true,
    bytes: true,
    needs: { w: 3, h: 3 },
  },
};

const BY_KIND = {
  // A program is not plain text: every name it binds carries the state of
  // the node it names, which is what makes the card a view of a running
  // computation rather than a file someone pasted in.
  code: { component: SourceEditor, mono: true, editable: true, source: true },
  note: { component: TextEditor, mono: false, editable: true },
  result: { component: ResultState, mono: true, editable: false, result: true },
  // A `print` and a `save` are both outputs the program declared, and they are
  // separate kinds because they are not the same act: a print is a value shown,
  // a save is an effect with a destination. They share a viewer only until the
  // save one exists -- at which point this is a row, not a rewrite.
  print: { component: ResultState, mono: true, editable: false, result: true },
  save: { component: ResultState, mono: true, editable: false, result: true },
};

const FALLBACK = { component: TextEditor, mono: true, editable: true };

/** The smallest a card is usable at, in cells, or `undefined`.
 *
 * A property of what the card *shows*, which is why it lives in this table and
 * not in the document: the file records the size somebody chose, and a floor is
 * not a choice. A stack adds a row per layer -- three overlays under a picture
 * is three lines of chrome, and a card that fits the picture but not the rows is
 * a card whose controls are off the bottom edge.
 */
export function needsFor(card, result = undefined) {
  const floor = viewerFor(card, result).needs;
  if (!floor) return undefined;
  const rows = (card?.parts?.length ?? 1) > 1 ? card.parts.length : 0;
  // A row is about a third of a cell; the navigation strip is another.
  const extra = Math.ceil((rows + (card?.index ? 1 : 0)) / 3);
  return { w: floor.w, h: floor.h + extra };
}

/**
 * The viewer for a card, and how it should be run.
 *
 * @param {{kind?: string}} card
 * @param {{valueType?: string}} [result] the value the card is showing, if any
 */
export function viewerFor(card, result = undefined) {
  if (result?.valueType && BY_RESULT_TYPE[result.valueType]) return BY_RESULT_TYPE[result.valueType];
  return BY_KIND[card?.kind] ?? FALLBACK;
}
