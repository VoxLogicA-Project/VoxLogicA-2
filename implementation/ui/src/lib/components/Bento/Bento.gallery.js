import Bento from "./Bento.svelte";

/** Each specimen needs its own card array: the board mutates what it is given,
 * and two specimens sharing one array would drag each other's cards. */
const board = (cards) => cards.map((card) => ({ auto: false, ...card }));

export default {
  name: "Bento",
  summary:
    "The board: cards placed on a lattice of cells, moved by their header, resized by their corner, sized to their content unless told otherwise. Positions are integer cells, never pixels.",
  component: Bento,
  axes: ["cards", "cols", "rows", "zoom", "page"],
  layout: "stack",
  variants: [
    {
      label: "placement",
      props: {
        cols: 6,
        rows: 4,
        label: "Placement example",
        cards: board([
          { id: "a", title: "wide", x: 0, y: 0, w: 4, h: 2 },
          { id: "b", title: "tall", x: 4, y: 0, w: 2, h: 4 },
          { id: "c", title: "small", x: 0, y: 2, w: 2, h: 2 },
        ]),
      },
    },
    {
      label: "constraints",
      props: {
        cols: 6,
        rows: 4,
        label: "Constraint example",
        cards: board([
          // Resize these: the first refuses to go under 2x2, the second holds
          // 2:1 whatever you do to it, the third will not exceed 3 cells wide.
          { id: "min", title: "min 2×2", x: 0, y: 0, w: 2, h: 2, minW: 2, minH: 2 },
          { id: "aspect", title: "aspect 2:1", x: 2, y: 0, w: 4, h: 2, aspect: 2 },
          { id: "max", title: "max w 3", x: 0, y: 2, w: 3, h: 2, maxW: 3 },
        ]),
      },
    },
    {
      label: "auto-sized to content",
      text: "This card was never given a width or a height: it measured what it holds and took the cells it needed, up to its maximum of four.",
      props: {
        cols: 6,
        rows: 4,
        label: "Auto-size example",
        cards: [
          { id: "auto", title: "auto", x: 0, y: 0, auto: true, w: 1, h: 1, maxW: 4 },
          { id: "fixed", title: "fixed 2×2", x: 4, y: 0, w: 2, h: 2 },
        ],
      },
    },
    {
      label: "paged",
      props: {
        cols: 6,
        rows: 3,
        label: "Paging example",
        cards: board([
          { id: "p0a", title: "page 1", x: 0, y: 0, w: 3, h: 2 },
          { id: "p1a", title: "page 2", x: 0, y: 0, w: 2, h: 3, page: 1 },
          { id: "p1b", title: "also page 2", x: 2, y: 0, w: 4, h: 1, page: 1 },
        ]),
      },
    },
  ],
};
