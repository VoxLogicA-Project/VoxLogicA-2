<script>
  /**
   * The board: a lattice of cells that cards are placed, moved and resized on.
   *
   * Positions are integer cell coordinates, never pixels. That is the whole
   * design: a layout you saved is the layout you get back on any screen, two
   * cards the same width *are* the same width, and "is this spot free" is a
   * decidable question rather than a hit test with a tolerance. The pixel size
   * of a cell is one CSS variable (`--bento-pitch`, in rem) times `zoom`, so
   * scaling the board never touches the model.
   *
   * A page is a bounded rectangle of cells, and it fills the space it is given:
   * `cols`/`rows` from the document are a *minimum*, and the board adds whatever
   * further whole cells fit on this screen. So the lattice is the viewport, a
   * card can be dragged and grown to the edge of it, and a layout authored on a
   * small screen still opens intact on a large one -- the cells it used are all
   * still there, with more beside them. Paging, not unbounded scrolling, is what
   * lets a card keep a meaning like "top-left of page 2".
   *
   * Placement is free and collisions are refused, not resolved: dropping a card
   * on occupied cells snaps it back. Cards that shove each other around are a
   * layout you cannot predict, and predictability is the point of a lattice.
   * Auto-packing, if it is ever wanted, belongs behind an explicit action.
   *
   * See doc/dev/ui-bento.md for the model, the units, and what is still open.
   */
  import ContextMenu from "../ContextMenu/ContextMenu.svelte";
  import BentoCard from "./BentoCard.svelte";
  import { SHORTCUTS } from "./shortcuts.js";

  let {
    /** `[{ id, x, y, w?, h?, page?, auto?, minW?, minH?, maxW?, maxH?, aspect?, title? }]` */
    cards = [],
    cols = 12,
    rows = 8,
    page = 0,
    zoom = 1,
    label = "Board",
    /** A gesture finished. The board does not apply it -- the owner of the
     * layout does, and the new layout comes back through `cards`.
     *
     * `onarrange` carries the whole result of one drag: the card that moved and
     * everyone it displaced, as `[{ id, x?, y?, w?, h? }]`. One gesture is one
     * change; sent as separate moves, the layout is briefly overlapping and
     * anything watching can see it that way. */
    onarrange = undefined,
    onpage = undefined,
    /** `(kind, x, y, w, h)` — a new card was asked for on empty cells. */
    onadd = undefined,
    /** `(id)` — a card was asked to go away. */
    onremove = undefined,
    /** `(id, page)` — a card was sent to another page. */
    onsendtopage = undefined,
    /** `(id, title)` — a card was renamed. */
    onrename = undefined,
    /** `(id, {x, y, w, h})` — a copy was asked for, and where it fits. */
    onduplicate = undefined,
    /** `(id, {x, y, w, h})` — a card showing what this one produces. */
    onderive = undefined,
    /** `(ids)` — the cards the user is working with. */
    onselect = undefined,
    selection = [],
    /** `{ ids, text }` — the selection as .imgql, ready before a drag starts.
     *
     * A DataTransfer may only be written inside `dragstart`, and what has to
     * travel is the server's own text -- the very thing the clipboard carries.
     * Asking for it when the drag begins would arrive too late, so the owner of
     * the board keeps it ready and hands it here. A drag that starts before it
     * is ready is refused rather than sent with some second, invented payload
     * that could then differ from what a paste would have produced. */
    cardsText = null,
    /** `(id | null)` — show one card alone, or the whole board again. */
    onfocus = undefined,
    /** `(zoom)` — the board was scaled. */
    onzoom = undefined,
    /** The shortcut list was asked for. */
    onhelp = undefined,
    /** `(id)` — Enter on a selected card: open whatever it holds. */
    onactivate = undefined,
    /** Cut, copy and paste of whole cards. The board only asks. */
    oncopycards = undefined,
    oncutcards = undefined,
    onpastecards = undefined,
    /** The card being shown alone, if any. */
    focus = null,
    /** What `onadd` may be asked for, and how big each starts. */
    kinds = [
      { kind: "code", label: "Code card", w: 5, h: 3 },
      { kind: "result", label: "Result card", w: 4, h: 3 },
      { kind: "note", label: "Note", w: 4, h: 2 },
    ],
    /** Snippet rendering a card's content; receives the card. */
    children,
  } = $props();

  /** The board renders a layout; it does not own one.
   *
   * Everything it knows arrives as props and every gesture leaves as a callback,
   * so the one copy of the truth is wherever the caller keeps it -- in the app,
   * the workspace replica, which the server owns. A board that also kept state
   * would be a second copy, and two copies of a layout is one bug waiting for
   * the day they disagree.
   */
  const items = $derived(cards.map((card) => ({ page: 0, auto: false, ...card })));

  /** Measured sizes for auto cards, which have no w/h of their own. Kept here
   * because occupancy is a question about the whole board. */
  let measured = $state({});

  let board = $state(null);
  let canvas = $state(null);
  /** Two hidden lengths, drawn from the same tokens the grid is drawn from.
   *
   * Measuring the *grid* for its pitch was the obvious thing and it was a
   * circle: the tracks are the pitch times the zoom, the zoom is capped by what
   * fits, and what fits is a question about the pitch. An effect that read the
   * zoom in order to divide it back out was therefore reading state it wrote,
   * and Svelte stopped it -- correctly -- with `effect_update_depth_exceeded`.
   *
   * A ruler has no such opinion. `--bento-pitch` and `--bento-gutter` are `rem`
   * and a spacing step, so their pixel values are the browser's to give and not
   * ours to compute; but they do not depend on the zoom, on the board, or on
   * anything this component writes. Measuring them is a question with an answer
   * rather than an echo. */
  let pitchRuler = $state(null);
  let gutterRuler = $state(null);
  /** Cell pitch at zoom 1, and the room the board has, both measured. */
  let basePitch = $state(0);
  let gutter = $state(0);
  let room_ = $state({ width: 0, height: 0 });

  /** The cells the cards on this page actually reach. */
  const needed = $derived(
    visible.reduce(
      (acc, card) => {
        const box = { ...sizeOf(card), x: card.x, y: card.y };
        return {
          cols: Math.max(acc.cols, box.x + box.w),
          rows: Math.max(acc.rows, box.y + box.h),
        };
      },
      { cols: 1, rows: 1 },
    ),
  );

  /** The room the board has, or the room the document says it wants.
   *
   * A tab that is not rendering -- hidden, backgrounded, driven by a test --
   * measures zero, and a board that believed that would collapse to a single
   * cell and refuse every move as out of bounds. The document's own `cols` and
   * `rows` are the answer when the window will not give one: a stated geometry
   * is better than a measured zero, and it makes the board behave identically
   * whether or not anyone is looking at it.
   */
  const space = $derived({
    width: room_.width || cols * basePitch,
    height: room_.height || rows * basePitch,
  });

  /** The largest cell size that still shows every card on this page.
   *
   * This board does not scroll, and that is a decision rather than an omission:
   * a bounded page you can see all of is the thing that makes a position mean
   * something, and half a board behind an edge is worse than a small board.
   * So zoom is a *wish* -- the cells grow until something would fall off, and
   * shrinking the window shrinks the cells rather than hiding a card.
   */
  const fitZoom = $derived(
    basePitch === 0
      ? 1
      : Math.min(
          (space.width + gutter) / (needed.cols * basePitch),
          (space.height + gutter) / (needed.rows * basePitch),
        ),
  );

  /** What the board is actually drawn at: the wish, capped by what fits. */
  const applied = $derived(Math.max(Math.min(zoom, fitZoom), 0.25));
  const pitch = $derived(basePitch * applied);

  /** The lattice is what fits, and never less than the cards reach. */
  const width = $derived(
    Math.max(needed.cols, pitch ? Math.floor((space.width + gutter) / pitch) : cols),
  );
  const height = $derived(
    Math.max(needed.rows, pitch ? Math.floor((space.height + gutter) / pitch) : rows),
  );

  /** Pages exist because cards are on them -- plus the empty one you are
   * standing on, if you asked for it.
   *
   * That is the whole of "add a page" and "remove a page": there is no list of
   * pages to keep in step with the cards, so a page cannot be missing and an
   * empty page cannot be left behind. Send the last card off page 3 and page 3
   * is gone; go to the page after the last and it is there while you are.
   */
  const pageCount = $derived(
    Math.max(1, page + 1, ...items.map((card) => card.page + 1)),
  );
  const visible = $derived(
    focus ? items.filter((card) => card.id === focus) : items.filter((card) => card.page === page),
  );

  /** A card's effective size: what it was given, or what it measured. */
  function sizeOf(card) {
    return { w: card.w ?? measured[card.id]?.w ?? 1, h: card.h ?? measured[card.id]?.h ?? 1 };
  }

  // The tokens own the geometry; JS asks the browser what they came to in
  // pixels rather than parsing `rem` itself. Two sources for one number is two
  // chances to disagree, and the drag maths has to land on the same cells the
  // grid drew -- `.board` derives its tracks from these same two variables, so
  // it draws what is measured here by construction.
  //
  // Nothing in this effect reads anything it writes, and nothing it observes
  // changes size because of what it writes: the rulers are fixed lengths, and
  // the canvas is a flex child whose size the board cannot influence. That is
  // the property that keeps it from looping, and it is worth preserving.
  $effect(() => {
    if (!pitchRuler || !gutterRuler || !canvas) return;
    const measure = () => {
      gutter = gutterRuler.getBoundingClientRect().width;
      const step = pitchRuler.getBoundingClientRect().width;
      if (step) basePitch = step;
      const box = canvas.getBoundingClientRect();
      room_ = { width: box.width, height: box.height };
    };
    measure();
    // The rulers move only when the root font size does; the canvas moves
    // whenever the window does. Both are worth hearing about.
    const observer = new ResizeObserver(measure);
    observer.observe(pitchRuler);
    observer.observe(gutterRuler);
    observer.observe(canvas);
    return () => observer.disconnect();
  });

  /** Where a card actually is: a spot committed a moment ago counts, even
   * though the layout has not come back from the server yet. Asking the props
   * alone is how a second card was allowed to grow over the first one's new
   * cells -- the answer was a round trip out of date. */
  function rectOf(card) {
    const size = sizeOf(card);
    const spot = pending[card.id];
    return {
      x: spot?.x ?? card.x,
      y: spot?.y ?? card.y,
      w: spot?.w ?? size.w,
      h: spot?.h ?? size.h,
    };
  }

  /** Cells [x, x+w) x [y, y+h) are inside the page and free of other cards. */
  function canPlace(id, x, y, w, h) {
    if (x < 0 || y < 0 || x + w > width || y + h > height) return false;
    return !visible.some((other) => {
      if (other.id === id) return false;
      const box = rectOf(other);
      return x < box.x + box.w && box.x < x + w && y < box.y + box.h && box.y < y + h;
    });
  }

  // ------------------------------------------------------- making room, live

  const overlaps = (a, b) =>
    a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  /** Where every card sits while `rect` is being dragged over the board.
   *
   * A pure function of the layout and the dragged rectangle, which is the whole
   * trick: cards do not "get pushed" and then need putting back. Each frame the
   * arrangement is recomputed from the untouched layout, so a card is displaced
   * exactly while the dragged card is on top of it and is home again the moment
   * it is not. There is no undo to get wrong, and no drift after a long drag.
   *
   * Displacement is a shove in the direction of travel first, and the nearest
   * free spot second -- the shove is what makes it read as sliding tiles rather
   * than as cards teleporting to wherever there was room. Returns `null` if
   * somebody cannot be housed, which is what makes the drop refuse.
   */
  function arrange(ids, rects, push) {
    const others = visible.filter((card) => !ids.includes(card.id));
    // Counted before anything is searched for. A drag big enough to leave less
    // free space than the cards it displaces cannot be arranged however hard
    // anyone looks, and looking is what used to cost the frame.
    const covered = [...rects.values()].reduce((total, rect) => total + rect.w * rect.h, 0);
    const wanted = others.reduce((total, card) => {
      const box = rectOf(card);
      return total + box.w * box.h;
    }, 0);
    if (covered + wanted > width * height) return null;
    const moving = [...rects.values()];
    const fixed = [];
    const homeless = [];
    for (const card of others) {
      const box = rectOf(card);
      (moving.some((rect) => overlaps(box, rect)) ? homeless : fixed).push({ card, box });
    }
    if (homeless.length === 0) return new Map();

    const anchor = moving[0];
    const taken = [...moving, ...fixed.map((entry) => entry.box)];
    const moved = new Map();
    // Nearest first: the card the dragged one has barely touched moves before
    // the card it is sitting on, which keeps the shuffle small and legible.
    homeless.sort(
      (a, b) =>
        Math.hypot(a.box.x - anchor.x, a.box.y - anchor.y) -
        Math.hypot(b.box.x - anchor.x, b.box.y - anchor.y),
    );

    for (const { card, box } of homeless) {
      const spot = findSpot(box, taken, push);
      if (!spot) return null;
      taken.push({ ...box, ...spot });
      moved.set(card.id, spot);
    }
    return moved;
  }

  /** How far a displaced card may be shoved before the drag is simply refused.
   *
   * Bounded for two reasons. A card that has to travel across the board to make
   * room has not "stepped aside", it has been *thrown* somewhere the eye cannot
   * follow -- so a short reach is the better behaviour whatever it costs. And
   * the search is cubic in the size of the board when nothing is free, which on
   * a 15x9 board with a maximized card measures 0.11ms a move: not a freeze,
   * but the wrong shape to leave in a loop that runs while a finger moves.
   */
  const REACH = 4;

  /** The closest free position for `box`, preferring the push direction. */
  function findSpot(box, taken, push) {
    const free = (x, y) =>
      x >= 0 &&
      y >= 0 &&
      x + box.w <= width &&
      y + box.h <= height &&
      !taken.some((other) => overlaps({ x, y, w: box.w, h: box.h }, other));

    // 1. Straight along the shove, as far as it takes to clear.
    if (push.dx || push.dy) {
      for (let step = 1; step <= REACH; step += 1) {
        const x = box.x + push.dx * step;
        const y = box.y + push.dy * step;
        if (free(x, y)) return { x, y };
      }
    }
    // 2. Otherwise the nearest cell that fits, by rings around home.
    for (let radius = 1; radius <= REACH; radius += 1) {
      for (let dy = -radius; dy <= radius; dy += 1) {
        for (let dx = -radius; dx <= radius; dx += 1) {
          if (Math.abs(dx) + Math.abs(dy) !== radius) continue;
          if (free(box.x + dx, box.y + dy)) return { x: box.x + dx, y: box.y + dy };
        }
      }
    }
    return null;
  }

  /** The gesture in flight: who is moving, where they are, and which way. */
  let drag = $state(null);
  const displaced = $derived(
    drag ? arrange([...drag.rects.keys()], drag.rects, drag.push) : null,
  );
  const refused = $derived(drag !== null && displaced === null);

  /** The cards a gesture on this card should carry.
   *
   * Dragging a selected card moves everything selected, by the same delta;
   * dragging an unselected one is about that card alone. That is the rule
   * every canvas uses, and it is the reason selection is worth having: what
   * you can do to one card you can do to the three you picked out. */
  function partyFor(id) {
    return selection.includes(id) ? selection.filter((other) => byId(other)) : [id];
  }

  const byId = (id) => visible.find((card) => card.id === id);

  /** A card leaving the board, on the system's own drag.
   *
   * Two flavours go on the transfer and they hold the same string: `text/plain`
   * so a card dropped in an editor, a terminal or a mail is readable program
   * text, and a private type so a drop that came from outside this application
   * is never mistaken for cards. The ids ride along because whoever receives
   * the drop has to know what to take away if it was a move.
   */
  function dragOut(card, event) {
    const ids = partyFor(card.id);
    const ready =
      cardsText &&
      ids.length === cardsText.ids.length &&
      ids.every((id) => cardsText.ids.includes(id));
    if (!ready || !event.dataTransfer) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData("text/plain", cardsText.text);
    event.dataTransfer.setData("text/voxlogica-cards", cardsText.text);
    event.dataTransfer.setData("text/voxlogica-card-ids", ids.join("\n"));
    event.dataTransfer.effectAllowed = "copyMove";
  }

  function preview(card, rect) {
    if (rect === null) {
      drag = null;
      return;
    }
    const party = partyFor(card.id);
    const lead = rectOf(card);
    const delta = { x: rect.x - lead.x, y: rect.y - lead.y, w: rect.w - lead.w, h: rect.h - lead.h };

    // Everyone moves by the same delta, and nobody leaves the page: the group
    // is clamped by whichever member hits the edge first, so the shape the
    // selection makes never changes on the way.
    const limits = party.reduce(
      (acc, id) => {
        const box = rectOf(byId(id));
        return {
          minX: Math.min(acc.minX, box.x),
          minY: Math.min(acc.minY, box.y),
          maxX: Math.max(acc.maxX, box.x + box.w),
          maxY: Math.max(acc.maxY, box.y + box.h),
        };
      },
      { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity },
    );
    const dx = Math.min(Math.max(delta.x, -limits.minX), width - limits.maxX);
    const dy = Math.min(Math.max(delta.y, -limits.minY), height - limits.maxY);

    const rects = new Map();
    for (const id of party) {
      const box = rectOf(byId(id));
      const moved =
        id === card.id
          ? { ...rect, x: box.x + dx, y: box.y + dy }
          : {
              x: box.x + dx,
              y: box.y + dy,
              w: Math.max(1, Math.min(box.w + delta.w, width - box.x - dx)),
              h: Math.max(1, Math.min(box.h + delta.h, height - box.y - dy)),
            };
      rects.set(id, moved);
    }

    const settled = drag?.rects.get(card.id);
    if (
      settled &&
      settled.x === rects.get(card.id).x &&
      settled.y === rects.get(card.id).y &&
      settled.w === rects.get(card.id).w &&
      settled.h === rects.get(card.id).h
    ) {
      // The pointer moved but nothing changed cell. Assigning a new object
      // anyway would invalidate the arrangement and recompute it for every
      // pixel of a drag rather than for every cell of one.
      return;
    }

    const before = settled ?? lead;
    // The push is the last actual movement, not the total: a card dragged left
    // and then back right shoves right, which is what the hand just did.
    const push = drag
      ? {
          dx: Math.sign(rects.get(card.id).x - before.x) || drag.push.dx,
          dy: Math.sign(rects.get(card.id).y - before.y) || drag.push.dy,
        }
      : { dx: 0, dy: 0 };
    drag = { id: card.id, rects, push };
  }

  // ------------------------------------------- holding a drop until it lands

  /** Positions already committed but not yet echoed back through `cards`.
   *
   * Without this the card snaps home for the length of one server round trip
   * and then jumps to where it was dropped -- the flash. The board keeps
   * drawing what it was told until the layout it is given agrees, or until the
   * grace period says the change was refused after all.
   */
  let pending = $state({});
  let pendingSince = 0;

  $effect(() => {
    if (Object.keys(pending).length === 0) return;
    // Read the layout so this re-runs whenever it changes.
    const settled = Object.fromEntries(
      Object.entries(pending).filter(([id, spot]) => {
        const card = items.find((entry) => entry.id === id);
        if (!card) return false;
        const size = sizeOf(card);
        const same =
          card.x === spot.x &&
          card.y === spot.y &&
          (spot.w === undefined || size.w === spot.w) &&
          (spot.h === undefined || size.h === spot.h);
        return !same;
      }),
    );
    if (Object.keys(settled).length !== Object.keys(pending).length) pending = settled;
    // A refusal never arrives as a change, so it has to time out. Long enough
    // for a round trip, short enough that a rejected drop does not linger.
    const timer = setTimeout(() => {
      if (Date.now() - pendingSince >= 900) pending = {};
    }, 900);
    return () => clearTimeout(timer);
  });

  function positionOf(card) {
    return pending[card.id] ?? { x: card.x, y: card.y };
  }

  /** Where a card is drawn right now: mid-drag arrangement first, then a
   * committed-but-unconfirmed spot, then the layout itself. */
  function placeOf(card) {
    // Focus is a way of looking, not a change to the layout: the card is drawn
    // filling the board and its real cells are left exactly as they were.
    if (focus === card.id) return { x: 0, y: 0, w: width, h: height };
    return drag?.rects.get(card.id) ?? displaced?.get(card.id) ?? positionOf(card);
  }

  // ------------------------------------------------ maximize, focus, zoom

  /** The largest free rectangle this card can grow into, from where it is.
   *
   * Grown one cell at a time in each direction, so it takes the room that is
   * actually free rather than shoving anyone: maximize is not a drag, and a
   * card that displaced three others because you double-clicked it would be a
   * surprise. Double-clicking a maximized card puts it back.
   */
  function maximize(card) {
    const size = sizeOf(card);
    const before = restored[card.id];
    if (before) {
      // Only if the old spot is still empty. A card that was maximized, then
      // dragged, then double-clicked would otherwise teleport back onto
      // whatever has since moved in -- which is exactly how two cards ended up
      // on the same cells.
      restored = { ...restored, [card.id]: undefined };
      if (canPlace(card.id, before.x, before.y, before.w, before.h)) {
        pending = { ...pending, [card.id]: before };
        pendingSince = Date.now();
        onarrange?.([{ id: card.id, ...before }]);
        return;
      }
    }
    let rect = { x: card.x, y: card.y, w: size.w, h: size.h };
    let grew = true;
    while (grew) {
      grew = false;
      for (const [dx, dy, dw, dh] of [
        [0, 0, 1, 0], // right
        [0, 0, 0, 1], // down
        [-1, 0, 1, 0], // left
        [0, -1, 0, 1], // up
      ]) {
        const next = {
          x: rect.x + dx,
          y: rect.y + dy,
          w: rect.w + dw,
          h: rect.h + dh,
        };
        if (canPlace(card.id, next.x, next.y, next.w, next.h)) {
          rect = next;
          grew = true;
        }
      }
    }
    if (rect.w === size.w && rect.h === size.h && rect.x === card.x && rect.y === card.y) return;
    restored = { ...restored, [card.id]: { x: card.x, y: card.y, w: size.w, h: size.h } };
    pending = { ...pending, [card.id]: rect };
    pendingSince = Date.now();
    onarrange?.([{ id: card.id, ...rect }]);
  }

  /** Where a maximized card came from, so double-click can undo itself. */
  let restored = $state({});

  /** The nearest free rectangle the same size as this card, for a copy.
   *
   * Beside it if there is room, then anywhere it fits: a duplicate that
   * refused to appear because the cell to the right was taken would be a copy
   * the user has to argue with. */
  function copySpot(card) {
    const size = sizeOf(card);
    const box = rectOf(card);
    for (const [dx, dy] of [[box.w, 0], [0, box.h], [-size.w, 0], [0, -size.h]]) {
      const spot = { x: box.x + dx, y: box.y + dy, ...size };
      if (canPlace(null, spot.x, spot.y, spot.w, spot.h)) return spot;
    }
    for (let y = 0; y + size.h <= height; y += 1) {
      for (let x = 0; x + size.w <= width; x += 1) {
        if (canPlace(null, x, y, size.w, size.h)) return { x, y, ...size };
      }
    }
    // A full page is not a refusal. Pages cost nothing and appear when used, so
    // the copy goes to the next one rather than the menu item quietly doing
    // nothing -- which is the worst thing a menu item can do.
    return { x: 0, y: 0, ...size, page: (card.page ?? 0) + 1 };
  }

  const ZOOMS = [0.6, 0.75, 0.875, 1, 1.25, 1.5, 2];

  function stepZoom(direction) {
    // Stepping from what is on screen, not from the stored wish: after the
    // board has been capped to fit, one press of zoom-out should visibly zoom
    // out rather than walk back down a wish nobody can see.
    const from = Math.min(zoom, fitZoom);
    const index = ZOOMS.findIndex((value) => value >= from - 0.001);
    const next = ZOOMS[Math.min(Math.max(index + direction, 0), ZOOMS.length - 1)];
    if (next !== zoom) onzoom?.(next);
  }

  /** Every action has a key, and every key is a chord.
   *
   * Bare letters belong to the cards: `f` is going to mean the letter f in a
   * program long before it means focus. So the board takes the platform's
   * modifier for everything except the arrows, which nothing else wants while
   * a card -- and not a text field -- has the keyboard.
   */
  /** Every action has a key, and every key is a chord.
   *
   * Bare letters belong to the cards: `f` is going to mean the letter f in a
   * program long before it means focus. So the board takes the platform's
   * modifier for everything except the arrows, which nothing else wants while a
   * card -- and not a text field -- has the keyboard. The list itself is in
   * ./shortcuts.js, next to nothing but the truth.
   */

  /** True when the keyboard belongs to something being typed into. */
  const typing = (event) =>
    event.target instanceof HTMLElement &&
    (event.target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName));

  function selected() {
    return selection.map((id) => byId(id)).filter(Boolean);
  }

  function onWindowKeydown(event) {
    if (typing(event)) return;

    if (event.key === "Escape") {
      if (focus) onfocus?.(null);
      else if (selection.length) onselect?.([]);
      return;
    }

    if (event.key === "Enter" && !event.metaKey && !event.ctrlKey && selection.length === 1) {
      // Enter opens the card, the way Enter opens anything a selection is on.
      event.preventDefault();
      onactivate?.(selection[0]);
      return;
    }

    if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
      event.preventDefault();
      onhelp?.();
      return;
    }

    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[
      event.key
    ];
    if (step && selection.length && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      nudge(step, event.shiftKey);
      return;
    }
    // The platform's own zoom chord, aimed at the board rather than the page:
    // this *is* what zooming means here, and leaving it to the browser would
    // scale the chrome around a board that already knows how to scale itself.
    if (!(event.metaKey || event.ctrlKey)) return;
    const key = event.key.toLowerCase();
    if (key === "=" || key === "+") {
      event.preventDefault();
      stepZoom(1);
    } else if (key === "-") {
      event.preventDefault();
      stepZoom(-1);
    } else if (key === "0") {
      event.preventDefault();
      if (zoom !== 1) onzoom?.(1);
    } else if (key === "a") {
      event.preventDefault();
      onselect?.(visible.map((card) => card.id));
    } else if (key === "f" && selection.length === 1) {
      event.preventDefault();
      onfocus?.(focus === selection[0] ? null : selection[0]);
    } else if (key === "backspace" || key === "delete") {
      event.preventDefault();
      for (const card of selected()) onremove?.(card.id);
      onselect?.([]);
    } else if (key === "enter" && selection.length) {
      event.preventDefault();
      for (const card of selected()) maximize(card);
    } else if (key === "d" && selection.length) {
      event.preventDefault();
      for (const card of selected()) onduplicate?.(card.id, copySpot(card));
    }
  }

  /** Move or resize everything selected, by one cell, together. */
  function nudge([dx, dy], resizing) {
    const party = selected();
    if (!party.length) return;
    const spots = party.map((card) => {
      const box = rectOf(card);
      return {
        id: card.id,
        ...(resizing
          ? { x: box.x, y: box.y, w: Math.max(1, box.w + dx), h: Math.max(1, box.h + dy) }
          : { x: box.x + dx, y: box.y + dy, w: box.w, h: box.h }),
      };
    });
    const ids = spots.map((spot) => spot.id);
    const room = spots.every(
      (spot) =>
        spot.x >= 0 &&
        spot.y >= 0 &&
        spot.x + spot.w <= width &&
        spot.y + spot.h <= height &&
        visible.every((other) => {
          // A card moving with the group cannot object to the group's move.
          if (ids.includes(other.id)) return true;
          return !overlaps(spot, rectOf(other));
        }),
    );
    if (!room) return;
    pending = { ...pending, ...Object.fromEntries(spots.map((spot) => [spot.id, spot])) };
    pendingSince = Date.now();
    onarrange?.(spots);
  }

  function onWheel(event) {
    // Pinch-to-zoom arrives as a ctrl-wheel; anything else is a scroll and is
    // none of our business.
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    stepZoom(event.deltaY < 0 ? 1 : -1);
  }

  function commit(card, rect) {
    // The whole arrangement is committed, not just the card under the finger:
    // everyone dragged with it, and everyone that stepped aside to make room.
    // A card that avoided the drag and then snapped back the moment the drag
    // ended would leave the two of them overlapping, which is the one state
    // this board is built never to be in.
    const spots = {};
    if (drag) for (const [id, spot] of drag.rects) spots[id] = spot;
    else spots[card.id] = rect;
    if (displaced) for (const [id, spot] of displaced) spots[id] = spot;
    pending = { ...pending, ...spots };
    pendingSince = Date.now();
    drag = null;
    // Whatever this card used to be restored to, it is not that card now.
    if (restored[card.id]) restored = { ...restored, [card.id]: undefined };

    const changed = Object.entries(spots)
      .map(([id, spot]) => {
        const before = items.find((entry) => entry.id === id);
        if (!before) return null;
        const size = sizeOf(before);
        const placement = { id };
        if (before.x !== spot.x) placement.x = spot.x;
        if (before.y !== spot.y) placement.y = spot.y;
        if (spot.w !== undefined && spot.w !== size.w) placement.w = spot.w;
        if (spot.h !== undefined && spot.h !== size.h) placement.h = spot.h;
        return Object.keys(placement).length > 1 ? placement : null;
      })
      .filter(Boolean);
    if (changed.length) onarrange?.(changed);
  }

  // ------------------------------------------------------------ adding cards

  /** The cell the last right-click landed on; where a new card goes. */
  let target = $state({ x: 0, y: 0 });

  /** The cell the pointer is over on empty lattice, or null over a card.
   *
   * A right-click menu is not an affordance: nobody right-clicks a blank area
   * to find out whether anything happens. Hovering free cells shows a faint
   * plus there, which is the whole invitation -- click it and a card exists.
   */
  let hovered = $state(null);

  function onBoardHover(event) {
    if (!pitch || drag || focus) {
      hovered = null;
      return;
    }
    if (event.target.closest(".card")) {
      hovered = null;
      return;
    }
    const cell = cellAt(event);
    hovered = cell && canPlace(null, cell.x, cell.y, 1, 1) ? cell : null;
  }

  /** The plus makes the first kind that fits, because a menu that opens onto a
   * menu is a menu too many. The other kinds are one right-click away. */
  function addHere() {
    if (!hovered) return;
    target = hovered;
    for (const kind of kinds) {
      const size = room(kind);
      if (size) {
        onadd?.(kind.kind, target.x, target.y, size.w, size.h);
        hovered = null;
        return;
      }
    }
  }

  // ------------------------------------------------- drawing a card's outline

  /** The rectangle being drawn on empty lattice, in cells. */
  let sketch = $state(null);
  let sketchFrom = null;

  /** Dragging across free cells says how big the card should be, which is the
   * one thing a click cannot say. Same gesture as selecting a range in a
   * spreadsheet, and it means the same thing: this rectangle, these cells. */
  function onBoardPointerDown(event) {
    if (event.button !== 0 || !pitch || focus || !onadd) return;
    // The plus sits exactly where a sketch starts, so it must not swallow the
    // press: dragging from it draws, clicking it makes the default card.
    if (event.target.closest(".card")) return;
    const cell = cellAt(event);
    if (!cell || !canPlace(null, cell.x, cell.y, 1, 1)) return;
    // Pressing empty lattice is how you let go of everything.
    if (selection.length) onselect?.([]);
    event.preventDefault();
    try {
      // Keeps the sketch alive when the pointer leaves the board mid-drag.
      board.setPointerCapture(event.pointerId);
    } catch {
      /* no active pointer with this id; the sketch still works, just leakier */
    }
    sketchFrom = cell;
    sketch = { ...cell, w: 1, h: 1 };
  }

  function onBoardPointerMove(event) {
    if (sketchFrom) {
      const cell = cellAt(event);
      if (!cell) return;
      const rect = {
        x: Math.min(sketchFrom.x, cell.x),
        y: Math.min(sketchFrom.y, cell.y),
        w: Math.abs(cell.x - sketchFrom.x) + 1,
        h: Math.abs(cell.y - sketchFrom.y) + 1,
      };
      // Grown only as far as it stays on free cells: the outline never promises
      // a card that could not be made.
      if (canPlace(null, rect.x, rect.y, rect.w, rect.h)) sketch = rect;
      return;
    }
    onBoardHover(event);
  }

  function onBoardPointerUp() {
    const rect = sketch;
    sketchFrom = null;
    sketch = null;
    if (!rect) return;
    // A click is a 1x1 sketch, and that is the plus's job -- it knows the size
    // each kind wants.
    if (rect.w === 1 && rect.h === 1) return;
    onadd?.(kinds[0].kind, rect.x, rect.y, rect.w, rect.h);
  }

  function cellAt(event) {
    const box = board.getBoundingClientRect();
    const cell = {
      x: Math.floor((event.clientX - box.left) / pitch),
      y: Math.floor((event.clientY - box.top) / pitch),
    };
    return cell.x >= 0 && cell.y >= 0 && cell.x < width && cell.y < height ? cell : null;
  }

  function onBoardContextMenu(event) {
    if (!pitch) return;
    const box = board.getBoundingClientRect();
    target = {
      x: Math.min(Math.max(Math.floor((event.clientX - box.left) / pitch), 0), width - 1),
      y: Math.min(Math.max(Math.floor((event.clientY - box.top) / pitch), 0), height - 1),
    };
  }

  /** The largest free rectangle at `target`, up to the kind's own size.
   *
   * A new card takes what is actually there rather than refusing to appear
   * because its default size would have overlapped something: clicking an empty
   * corner should produce a card, not a shrug. `null` when even one cell is
   * taken, which is the only honest refusal.
   */
  function room(kind) {
    if (!canPlace(null, target.x, target.y, 1, 1)) return null;
    let w = 1;
    let h = 1;
    while (w < kind.w && canPlace(null, target.x, target.y, w + 1, h)) w += 1;
    while (h < kind.h && canPlace(null, target.x, target.y, w, h + 1)) h += 1;
    return { w, h };
  }

  const addItems = $derived([
    ...(onpastecards ? [{ label: "Paste cards", hint: "mod+V", onselect: onpastecards }, { separator: true }] : []),
    ...kinds.map((kind) => {
      const size = room(kind);
      return {
        label: kind.label,
        hint: size ? `${size.w}×${size.h}` : "no room",
        disabled: size === null,
        onselect: () => onadd?.(kind.kind, target.x, target.y, size.w, size.h),
      };
    }),
  ]);
</script>

<svelte:window onkeydown={onWindowKeydown} />

<div class="bento" class:focusing={focus !== null}>
  <!-- The empty lattice is where a card comes from: right-click a free cell and
       the menu offers the kinds that fit there. A toolbar button would have to
       guess where the card goes; the cell you pointed at does not. -->
  <div
    bind:this={canvas}
    class="canvas"
    oncontextmenucapture={onBoardContextMenu}
    onwheel={onWheel}
  >
    <!-- Not decoration and not layout: these are the two lengths the grid is
         built from, held somewhere the zoom cannot reach them. See the effect
         that measures them. -->
    <div class="ruler pitch" bind:this={pitchRuler} aria-hidden="true"></div>
    <div class="ruler gutter" bind:this={gutterRuler} aria-hidden="true"></div>
    <ContextMenu label="Board" items={addItems}>
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        bind:this={board}
        class="board"
        class:arranging={drag !== null}
        onpointerdown={onBoardPointerDown}
        onpointermove={onBoardPointerMove}
        onpointerup={onBoardPointerUp}
        onpointercancel={onBoardPointerUp}
        onpointerleave={() => (hovered = null)}
        role="group"
        aria-label={label}
        style="--cols: {width}; --rows: {height}; --zoom: {applied};"
      >
        {#if sketch}
          <div
            class="sketch"
            style="transform: translate3d({sketch.x * pitch}px, {sketch.y * pitch}px, 0);
                   width: {sketch.w * pitch - gutter}px; height: {sketch.h * pitch - gutter}px;"
          >
            <span class="numeric">{sketch.w}×{sketch.h}</span>
          </div>
        {/if}

        {#if hovered && !sketch && onadd}
          <button
            type="button"
            class="add"
            aria-label="New card at column {hovered.x + 1}, row {hovered.y + 1}"
            style="transform: translate3d({hovered.x * pitch}px, {hovered.y * pitch}px, 0);
                   width: {pitch - gutter}px; height: {pitch - gutter}px;"
            onclick={addHere}
            onpointerdown={onBoardPointerDown}
          >
            +
          </button>
        {/if}

        {#each visible as card (card.id)}
          <BentoCard
            {card}
            {pitch}
            {gutter}
            cols={width}
            rows={height}
            at={placeOf(card)}
            invalid={refused && drag?.id === card.id}
            size={sizeOf(card)}
            focused={focus === card.id}
            onremove={onremove ? () => onremove(card.id) : undefined}
            selected={selection.includes(card.id)}
            onselect={onselect
              ? (additive, collapse) =>
                  onselect(
                    additive
                      ? selection.includes(card.id)
                        ? selection.filter((id) => id !== card.id)
                        : [...selection, card.id]
                      : collapse || !selection.includes(card.id)
                        ? [card.id]
                        : selection,
                  )
              : undefined}
            onduplicate={onduplicate ? () => onduplicate(card.id, copySpot(card)) : undefined}
            oncopy={oncopycards}
            oncut={oncutcards}
            ondragout={cardsText ? (event) => dragOut(card, event) : undefined}
            onderive={onderive ? () => onderive(card.id, copySpot(card)) : undefined}
            onmaximize={() => maximize(card)}
            onrename={onrename ? (title) => onrename(card.id, title) : undefined}
            onfocus={onfocus ? (on) => onfocus(on ? card.id : null) : undefined}
            onsendtopage={onsendtopage && !focus
              ? (next) => onsendtopage(card.id, Math.max(0, next))
              : undefined}
            onpreview={(rect) => preview(card, rect)}
            oncommit={(rect) => commit(card, rect)}
            onmeasure={(w, h) => (measured = { ...measured, [card.id]: { w, h } })}
          >
            {@render children?.(card)}
          </BentoCard>
        {/each}
      </div>
    </ContextMenu>
  </div>

  {#if focus}
    <nav class="pager" aria-label="Focus">
      <button type="button" onclick={() => onfocus?.(null)}>Leave focus <kbd>esc</kbd></button>
    </nav>
  {:else if pageCount > 1 || onpage}
    <nav class="pager" aria-label="Board pages">
      <button
        type="button"
        aria-label="Previous page"
        disabled={page === 0}
        onclick={() => onpage?.(page - 1)}
      >
        ‹
      </button>
      <span class="numeric">page {page + 1} / {pageCount}</span>
      <button
        type="button"
        aria-label={page >= pageCount - 1 ? "New page" : "Next page"}
        onclick={() => onpage?.(page + 1)}
      >
        {page >= pageCount - 1 ? "+" : "›"}
      </button>
    </nav>
  {/if}
</div>

<style>
  .bento {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
    /* The board takes the room it is given; the pager keeps its own line. */
    flex: 1;
    min-height: 0;
  }

  .canvas {
    position: relative;
    flex: 1;
    min-height: 0;
    min-width: 0;
  }

  /* Out of flow and of no height, so the board lays out as if they were not
     there -- which, as far as anything but the tape measure is concerned, they
     are not. */
  .ruler {
    position: absolute;
    top: 0;
    left: 0;
    height: 0;
    visibility: hidden;
    pointer-events: none;
  }

  .ruler.pitch {
    width: var(--bento-pitch);
  }

  .ruler.gutter {
    width: var(--bento-gutter);
  }

  .board {
    /* One cell is `pitch - gutter`; a card `w` cells wide spans `w` tracks plus
     * the `w - 1` gutters between them, which is exactly `w * pitch - gutter`. */
    --pitch: calc(var(--bento-pitch) * var(--zoom));
    --cell: calc(var(--pitch) - var(--bento-gutter));
    display: grid;
    grid-template-columns: repeat(var(--cols), var(--cell));
    grid-template-rows: repeat(var(--rows), var(--cell));
    gap: var(--bento-gutter);
    /* Exactly as wide as the lattice, so the dotted grid below marks cells that
     * exist rather than implying a board that continues past its own edge. */
    width: max-content;
    /* The tracks draw the lattice and give the board its size; the cards sit on
     * top of it, positioned by transform. A grid item cannot be animated
     * between cells -- grid lines do not interpolate -- and re-laying out the
     * whole grid on every pointer move is what made dragging flicker. */
    position: relative;
    /* The lattice is not painted on the board itself: it lives in a layer that
     * can fade. It answers "where will this land", which is a question nobody
     * is asking until they have a card in their hand. */
  }

  .board::before {
    content: "";
    position: absolute;
    inset: 0;
    background-image: radial-gradient(
      circle at 1px 1px,
      var(--color-border) 1px,
      transparent 0
    );
    background-size: var(--pitch) var(--pitch);
    background-position: calc(var(--bento-gutter) / -2) calc(var(--bento-gutter) / -2);
    opacity: 0;
    transition: opacity var(--motion-base) var(--easing-standard);
    pointer-events: none;
  }

  .board.arranging::before {
    opacity: 1;
  }

  /* The outline of a card that does not exist yet, so it is drawn as an
   * intention: the card's own shape, in the accent, with the size it would be. */
  .sketch {
    position: absolute;
    top: 0;
    left: 0;
    display: grid;
    place-items: center;
    border-radius: var(--radius-lg);
    background: var(--color-accent-subtle);
    color: var(--color-text-muted);
    font-size: var(--text-2xs);
    pointer-events: none;
  }

  /* Faint until you are on it: an invitation, not a control panel. */
  .add {
    position: absolute;
    top: 0;
    left: 0;
    display: grid;
    place-items: center;
    border-radius: var(--radius-md);
    color: var(--color-text-subtle);
    font-size: var(--text-md);
    opacity: 0.5;
    transition: opacity var(--motion-fast) var(--easing-standard),
      background var(--motion-fast) var(--easing-standard);
  }

  .add:hover {
    opacity: 1;
    background: var(--color-surface);
  }

  .pager {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    align-self: center;
    font-size: var(--text-xs);
    color: var(--color-text-muted);
  }

  /* No outline: the pager is three quiet words at the foot of the board, and a
   * bordered button for "next page" would be the loudest thing on screen. */
  .pager button {
    padding: 0 var(--space-2);
    border-radius: var(--radius-sm);
    color: var(--color-text-subtle);
  }

  .pager button:hover:not(:disabled) {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .pager button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
</style>
