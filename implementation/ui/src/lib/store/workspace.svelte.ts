// The workspace replica.
//
// The server owns the document; this is the browser's copy of it, kept current
// by `workspace` events over the WebSocket. Nothing in the UI writes to it --
// components read it and call actions (src/lib/actions), the server applies
// them, and the state comes back here. That is what makes an agent driving the
// MCP server and a person driving the browser two views of one workspace rather
// than two workspaces that have to be reconciled.
//
// See doc/dev/ui-workspace.md sections 3 and 4.

export type CardKind = "code" | "result" | "note";

export interface Card {
  id: string;
  kind: CardKind;
  x: number;
  y: number;
  page: number;
  /** Absent when the card has never been sized by hand: the board measures it. */
  w?: number;
  h?: number;
  minW?: number;
  minH?: number;
  maxW?: number;
  maxH?: number;
  aspect?: number;
  auto: boolean;
  title?: string;
  /** Code cards only: the program text this card holds. */
  source?: string;
  /** Result cards only: the node this card is a view of. */
  node?: string;
  view?: string;
}

export interface Board {
  cols: number;
  rows: number;
}

export interface View {
  page: number;
  zoom: number;
  selection: string | null;
  /** One card shown alone, or null for the whole board. */
  focus: string | null;
}

export interface Snapshot {
  path: string | null;
  board: Board;
  cards: Card[];
  view: View;
  dirty: boolean;
}

class WorkspaceStore {
  board = $state<Board>({ cols: 12, rows: 8 });
  cards = $state<Card[]>([]);
  view = $state<View>({ page: 0, zoom: 1, selection: null, focus: null });
  path = $state<string | null>(null);
  dirty = $state(false);
  /** True once the server has told us anything at all. */
  loaded = $state(false);

  visible = $derived(this.cards.filter((card) => card.page === this.view.page));

  pages = $derived(Math.max(1, ...this.cards.map((card) => card.page + 1)));

  card(id: string): Card | undefined {
    return this.cards.find((card) => card.id === id);
  }

  /** Replace the replica wholesale. The server sends whole snapshots because a
   * document this size costs less to send than a patch protocol costs to get
   * right, and a whole snapshot cannot drift. */
  receive(snapshot: Snapshot): void {
    this.board = snapshot.board;
    this.cards = snapshot.cards;
    this.view = snapshot.view;
    this.path = snapshot.path;
    this.dirty = snapshot.dirty;
    this.loaded = true;
  }
}

export const workspace = new WorkspaceStore();
