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
  setKind: (id: string, kind: string) => invoke("card.setKind", { id, kind }),
  setViewMode: (id: string, view: string) => invoke("card.setViewMode", { id, view }),
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
};

export const view = {
  goToPage: (page: number) => invoke("view.goToPage", { page }),
  setZoom: (zoom: number) => invoke("view.setZoom", { zoom }),
  select: (ids: string[]) => invoke("view.select", { ids }),
  focus: (id: string | null) => invoke("view.focus", id === null ? {} : { id }),
};

export const workspace = {
  open: (path: string) => invoke<boolean>("workspace.open", { path }),
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
