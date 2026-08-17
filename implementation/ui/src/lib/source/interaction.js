// How many clicks it takes before a card can be typed into.
//
// One switch, read in one place, because the two behaviours differ only in
// *when* the content starts taking the keyboard -- not in what happens
// afterwards. Two code paths for that would be two things to keep working.
//
//   "click"  a click puts the caret where you clicked and typing starts. One
//            click, no mode, the way every editor on the machine behaves.
//
//   "focus"  the first click gives the card the keyboard and the second
//            interacts. Worth having for a board where a stray click on a
//            slider or a slice would change a value rather than select a card.
//
// `click` today, because pressing Enter to begin typing is a step nobody asked
// for and the thing being edited is text far more often than it is a control.
//
// The board's own chords are never in doubt either way: while a card has the
// keyboard, its surface stops the events (see `onKeydown` in SourceEditor), so
// a letter is a letter and never a shortcut.
export const INTERACTION = "click";
