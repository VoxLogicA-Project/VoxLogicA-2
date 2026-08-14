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
  removeCard: (id: string) => invoke("board.removeCard", { id }),
  setPage: (id: string, page: number) => invoke("board.setPage", { id, page }),
};

export const card = {
  setTitle: (id: string, title: string) => invoke("card.setTitle", { id, title }),
  setSource: (id: string, text: string) => invoke("card.setSource", { id, text }),
  bindNode: (id: string, node: string) => invoke("card.bindNode", { id, node }),
  setKind: (id: string, kind: string) => invoke("card.setKind", { id, kind }),
  setViewMode: (id: string, view: string) => invoke("card.setViewMode", { id, view }),
};

export const view = {
  goToPage: (page: number) => invoke("view.goToPage", { page }),
  setZoom: (zoom: number) => invoke("view.setZoom", { zoom }),
  select: (id: string | null) => invoke("view.select", id === null ? {} : { id }),
};

export const workspace = {
  open: (path: string) => invoke<boolean>("workspace.open", { path }),
  export: () => invoke<string>("workspace.export"),
  save: (path?: string) => invoke<string>("workspace.save", path ? { path } : {}),
};

export const actions = { board, card, view, workspace };
