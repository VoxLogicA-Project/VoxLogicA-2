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
  /** The reference: stable, never shown, what other cards name this one by. */
  id: string;
  /** The name: prose, editable, free to collide with another card's. */
  title: string;
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
  /** Code cards only: the program text this card holds. */
  source?: string;
  /** Result cards only: the node this card is a view of. */
  node?: string;
  /** The card this one was derived from, by id. A derived card follows its
   * source: it exists to show something that card produces. */
  from?: string;
  view?: string;
}

export interface Board {
  cols: number;
  rows: number;
}

export interface View {
  page: number;
  zoom: number;
  selection: string[];
  /** One card shown alone, or null for the whole board. */
  focus: string | null;
  /** How far back the board stands: "source", "both" or "value". */
  lens: string;
  /** "board" or "document": the two distances Tab swaps between. */
  showing: string;
}

export interface LibraryFile {
  path: string;
  /** The file's own name, without the suffix. */
  name: string;
  /** The project it is in, or null for a loose file at the top. */
  project: string | null;
  open: boolean;
  modified: number;
}

export interface LibraryProject {
  name: string;
  path: string;
  /** True when the folder lives outside the library and was linked in. */
  linked: boolean;
}

export interface Library {
  root: string;
  projects: LibraryProject[];
  files: LibraryFile[];
}

export interface Issues {
  /** Cards that need each other. No order satisfies them. */
  cycle: string[];
  /** Names defined by more than one card, and which cards define them. */
  duplicates: Record<string, string[]>;
}

export interface Snapshot {
  path: string | null;
  /** The workspace's folder name -- what a person calls this piece of work. */
  name: string | null;
  board: Board;
  cards: Card[];
  view: View;
  dirty: boolean;
  /** The document as it would be written to disk. */
  source: string;
  library: Library;
  issues: Issues;
  /** Which node each `let` in this document compiled to. */
  nodes: Record<string, string>;
  canUndo: boolean;
  canRedo: boolean;
}

class WorkspaceStore {
  board = $state<Board>({ cols: 12, rows: 8 });
  cards = $state<Card[]>([]);
  view = $state<View>({ page: 0, zoom: 1, selection: [], focus: null, lens: "both", showing: "board" });
  path = $state<string | null>(null);
  name = $state<string | null>(null);
  dirty = $state(false);
  source = $state("");
  library = $state<Library>({ root: "", projects: [], files: [] });
  issues = $state<Issues>({ cycle: [], duplicates: {} });
  /** name -> node hash, as the reducer compiled this document. A property of
   * the text, so it arrives with the text and is replaced with it. */
  nodes = $state<Record<string, string>>({});
  canUndo = $state(false);
  canRedo = $state(false);
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
    this.name = snapshot.name ?? null;
    this.dirty = snapshot.dirty;
    this.source = snapshot.source ?? "";
    this.library = snapshot.library ?? { root: "", projects: [], files: [] };
    this.issues = snapshot.issues ?? { cycle: [], duplicates: {} };
    this.nodes = snapshot.nodes ?? {};
    this.canUndo = snapshot.canUndo ?? false;
    this.canRedo = snapshot.canRedo ?? false;
    this.loaded = true;
  }
}

export const workspace = new WorkspaceStore();
