// The action namespace.
//
// Hierarchical by what the user is doing -- a board, a card, a view, a workspace
// -- rather than by what the code is made of. Somebody looking for "how do I
// move a card" finds `board.moveCard` without first learning what a Document is.
//
// Every name here exists in the server's manifest
// (implementation/python/voxlogica/ui/actions.py) and a test fails if the two
// lists ever disagree. None of these modules render anything: they are the
// mutation surface, and the components that call them have none of their own.

import { invoke } from "./dispatch.svelte.ts";

export const board = {
  moveCard: (id: string, x: number, y: number) => invoke("board.moveCard", { id, x, y }),
  resizeCard: (id: string, w: number, h: number) => invoke("board.resizeCard", { id, w, h }),
  /** One drag that displaced others is one change, not a burst of moves. */
  arrange: (cards: Array<{ id: string; x?: number; y?: number; w?: number; h?: number }>) =>
    invoke("board.arrange", { cards }),
  addCard: (id: string, params: Record<string, unknown> = {}) =>
    invoke("board.addCard", { id, ...params }),
  /** A card that shows something another card produces; it records `from`. */
  deriveCard: (id: string, newId: string, params: Record<string, unknown> = {}) =>
    invoke("board.deriveCard", { id, newId, ...params }),
  /** The clipboard's format is the file's format: .imgql text, both ways. */
  copyCards: (ids: string[]) => invoke<string>("board.copyCards", { ids }),
  cutCards: (ids: string[]) => invoke<string>("board.cutCards", { ids }),
  pasteCards: (text: string, params: Record<string, unknown> = {}) =>
    invoke<string[]>("board.pasteCards", { text, ...params }),
  removeCard: (id: string) => invoke("board.removeCard", { id }),
  duplicateCard: (id: string, newId: string, params: Record<string, unknown> = {}) =>
    invoke("board.duplicateCard", { id, newId, ...params }),
  setPage: (id: string, page: number) => invoke("board.setPage", { id, page }),
};

export const card = {
  setTitle: (id: string, title: string) => invoke("card.setTitle", { id, title }),
  setSource: (id: string, text: string) => invoke("card.setSource", { id, text }),
  bindNode: (id: string, node: string) => invoke("card.bindNode", { id, node }),
  /** Which of the card's bindings it is about. Omit to go back to the default,
   * which is the last one the fragment declares. */
  setFocus: (id: string, focus?: string) =>
    invoke("card.setFocus", focus ? { id, focus } : { id }),
  setKind: (id: string, kind: string) => invoke("card.setKind", { id, kind }),
  setViewMode: (id: string, view: string) => invoke("card.setViewMode", { id, view }),
  /** Declare what this card is about as an output of the program: the
   * directive is written into the text, where a diff and a headless run can
   * both see it. A button that wrote a file would be an effect with no record. */
  saveThis: (id: string, label?: string) =>
    invoke<string>("card.saveThis", label ? { id, label } : { id }),
  printThis: (id: string, label?: string) =>
    invoke<string>("card.printThis", label ? { id, label } : { id }),
  /** Compute what this card is about. Its dependencies follow on their own:
   * they are other cards' bindings, which are the same hashes. */
  run: (id: string) => invoke<Record<string, unknown>>("card.run", { id }),
};

/** Node states, asked of the server rather than read from the replica.
 *
 * The browser has its own `results` store and that is what a *card* reads --
 * reactively, off the pushed events. These two exist for the other reader: code
 * that wants an answer once, and code that wants to wait for one, without
 * standing up a subscription to get it. Same vocabulary as the MCP server, so
 * an agent and a script say the same thing. */
export const resultsActions = {
  get: (node: string) => invoke<Record<string, unknown>>("results.get", { node }),
  wait: (node: string, state = "done", timeout = 60) =>
    invoke<Record<string, unknown>>("results.wait", { node, state, timeout }),
};

/** The library: the projects and files this instance can open.
 *
 * One file is open at a time and the sidebar lists them all, so there are no
 * tabs -- a tab bar is a second, worse copy of a list you already have. */
export const library = {
  open: (path: string) => invoke<boolean>("library.open", { path }),
  newFile: (project?: string, name?: string) =>
    invoke<string>("library.newFile", { ...(project ? { project } : {}), ...(name ? { name } : {}) }),
  newProject: (name: string) => invoke<string>("library.newProject", { name }),
  moveFile: (path: string, project: string | null) =>
    invoke<string>("library.moveFile", { path, ...(project ? { project } : {}) }),
  copyFile: (path: string, project: string | null) =>
    invoke<string>("library.copyFile", { path, ...(project ? { project } : {}) }),
  renameFile: (path: string, name: string) =>
    invoke<string>("library.renameFile", { path, name }),
  renameProject: (name: string, to: string) =>
    invoke<string>("library.renameProject", { name, to }),
  deleteFile: (path: string) => invoke<boolean>("library.deleteFile", { path }),
  /** With no path, the system's own folder chooser opens. */
  /** Empty projects only; a project with files in it is deleted file by file. */
  deleteProject: (name: string) => invoke<boolean>("library.deleteProject", { name }),
  addFolder: (path?: string) => invoke("library.addFolder", path ? { path } : {}),
  forgetFolder: (path: string) => invoke<boolean>("library.forgetFolder", { path }),
  reveal: (path: string) => invoke<boolean>("library.reveal", { path }),
  /** Cards dropped on a row: the clipboard's text, merged into that file.
   *
   * The same .imgql `board.copyCards` produces, through the same merge, so a
   * card dragged into a file and a card pasted into it land identically. */
  /** A label, written into the file itself so it travels with it. */
  addLabel: (path: string, label: string) =>
    invoke<boolean>("library.addLabel", { path, label }),
  removeLabel: (path: string, label: string) =>
    invoke<boolean>("library.removeLabel", { path, label }),
  pasteCards: (path: string, text: string) =>
    invoke<string[]>("library.pasteCards", { path, text }),
};

export const view = {
  goToPage: (page: number) => invoke("view.goToPage", { page }),
  setZoom: (zoom: number) => invoke("view.setZoom", { zoom }),
  /** The board, or the document it is drawn from. */
  show: (showing: string) => invoke("view.show", { showing }),
  /** How far back the board stands from its cards: source, both or value. */
  setLens: (lens: string) => invoke("view.setLens", { lens }),
  select: (ids: string[]) => invoke("view.select", { ids }),
  focus: (id: string | null) => invoke("view.focus", id === null ? {} : { id }),
};

export const workspace = {
  open: (path: string) => invoke<boolean>("workspace.open", { path }),
  tidy: () => invoke<boolean>("workspace.tidy"),
  export: () => invoke<string>("workspace.export"),
  setText: (text: string) => invoke<boolean>("workspace.setText", { text }),
  save: (path?: string) => invoke<string>("workspace.save", path ? { path } : {}),
  /** Give a scratch workspace a home -- typically inside a repository. */
  moveTo: (path: string) => invoke<string>("workspace.moveTo", { path }),
  /** Rename the workspace, which renames the folder it lives in. */
  rename: (name: string) => invoke<string>("workspace.rename", { name }),
  /** The system's own save panel; the move happens when it is answered. */
  chooseLocation: () => invoke<boolean>("workspace.chooseLocation"),
  reveal: () => invoke<boolean>("workspace.reveal"),
  undo: () => invoke<boolean>("workspace.undo"),
  redo: () => invoke<boolean>("workspace.redo"),
};

export const actions = { board, card, library, view, workspace };
