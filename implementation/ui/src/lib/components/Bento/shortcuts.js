// What the board answers to, in one list.
//
// Beside the code that implements it on purpose: a list of shortcuts kept
// somewhere else is a list that is wrong by the second release. The help sheet
// renders this; `Bento` acts on it.
//
// `mod` stands for the platform's own modifier -- whoever displays these puts
// the right symbol in.

export const SHORTCUTS = [
  { keys: "drag", does: "Move a card. Selected cards move together." },
  { keys: "drag an edge", does: "Resize from any side or corner." },
  { keys: "drag empty cells", does: "Draw a new card at that size." },
  { keys: "click the +", does: "New card on the cell you are pointing at." },
  { keys: "double-click", does: "Maximize into the free room; again to restore." },
  { keys: "double-click the name / F2", does: "Rename in place. Enter or Tab keeps it, Escape reverts." },
  { keys: "Enter", does: "Edit the selected card. mod+Enter keeps it, Escape abandons." },
  { keys: "long press", does: "Focus this card alone; again to leave." },
  { keys: "shift-click", does: "Add a card to the selection." },
  { keys: "arrows", does: "Move the selection one cell." },
  { keys: "shift+arrows", does: "Resize the selection one cell." },
  { keys: "mod+F", does: "Focus the selected card, or leave focus." },
  { keys: "mod+Backspace", does: "Delete the selection." },
  { keys: "mod+Enter", does: "Maximize the selection." },
  { keys: "mod+D", does: "Duplicate the selection." },
  { keys: "mod+A", does: "Select every card on the page." },
  { keys: "mod+Z / shift+mod+Z", does: "Undo, redo." },
  { keys: "mod+= / mod+- / mod+0", does: "Zoom in, out, reset." },
  { keys: "Escape", does: "Leave focus, or clear the selection." },
  { keys: "Tab", does: "Switch between the board and the document." },
  { keys: "mod+B", does: "Hide or show the file list." },
  { keys: "mod+X / mod+C / mod+V", does: "Cut, copy and paste cards — or files, in the list." },
  { keys: "mod+L", does: "How close to stand: the program, both, or just the value." },
  { keys: "mod+R", does: "New result card from the selection." },
  { keys: "mod+→ / mod+←", does: "Send the selection to the next or previous page." },
  { keys: "mod+K", does: "Filter the file list." },
  { keys: "mod+U", does: "Sort the file list by name or by last changed." },
  { keys: "mod+N", does: "New file, where the open one lives." },
  { keys: "mod+E", does: "Show a file in the folder it is in." },
  { keys: "shift+mod+P", does: "New project." },
  { keys: "shift+mod+O", does: "Add a folder you already have." },
  { keys: "?", does: "This list." },
];
