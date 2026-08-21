<script>
  /**
   * One card on the board: placed by cell, moved by its header, resized by its
   * corner, and sized to its own content when nothing says otherwise.
   *
   * Internal to `Bento` -- it needs the board's pitch and the arrangement the
   * board computes, so it is not exported from the library on its own.
   *
   * Positioned by transform, not by grid lines. Two reasons, both visible: a
   * transform does not relayout the board on every pointer move, which is what
   * made dragging flicker, and a transform is the only thing here that can be
   * transitioned, which is what makes a displaced card *slide* out of the way
   * instead of teleporting. The card under the pointer has transitions off: it
   * snaps cell to cell, and easing a snap only blurs where it landed.
   *
   * Auto-sizing measures the content at `max-content` for a single frame and
   * rounds up to whole cells, which is why it can shrink as well as grow: a
   * card that only ever grew would keep the size of the largest thing it ever
   * held. The measurement is bounded by the card's own max constraints, so a
   * long line wraps instead of asking for a card wider than the page.
   *
   * The card reports gestures and renders what it is told. It never applies a
   * move to itself: the board decides what the whole arrangement becomes, and
   * the owner of the layout decides whether that is what happens.
   */
  import ContextMenu from "../ContextMenu/ContextMenu.svelte";

  let {
    card,
    /** The card's effective size in cells: given, or last measured. */
    size,
    /** Where the board says this card sits right now, in cells. */
    at,
    pitch,
    gutter,
    cols,
    rows,
    /** True when the board could not arrange the drag this card is leading. */
    invalid = false,
    /** `(rect | null)` while a gesture runs; the board arranges around it. */
    onpreview,
    /** `(rect)` when the gesture is released and should be kept. */
    oncommit,
    onmeasure,
    onremove,
    onmaximize,
    onfocus,
    onsendtopage,
    onrename,
    onduplicate,
    onderive,
    oncopy,
    oncut,
    /** `(event)` on `dragstart` — the card is being dragged off the board.
     *
     * The board fills the DataTransfer, because what travels is the whole
     * selection's text and only the board knows what is selected. Refusing the
     * drag is `event.preventDefault()` inside it, as it is anywhere else. */
    ondragout,
    /** `(ids)` when cards are dropped onto this one. The board's own arrange
     * gesture is pointer-driven and starts on the header, so an HTML5 drop
     * here is unambiguous: it came from another card's body. */
    ondropcards,
    /** `(card) -> {w, h} | undefined` — the floor, asked rather than carried. */
    floorOf,
    /** `[{id, title}]` — the other cards this one could be laid over, and
     * `(id)` to do it.
     *
     * A menu as well as a gesture, and not as a fallback. Alt-drag is fast once
     * you know it and undiscoverable until then, and a modifier is also the part
     * of a gesture most likely to be eaten by a window manager. A named item you
     * can read is the one route that cannot quietly not happen. */
    layTargets = [],
    onlayover,
    /** True while a drop in flight would be laid over this card. Drawn, because
     * a rule you can see beats a modifier you were told about once. */
    laying = false,
    onselect,
    /** The card was asked to compute what it is about. Absent = no button. */
    onrun,
    /** `(lens)` — this card shows something other than what the board shows.
     * An empty string puts it back to following the board. */
    onlens,
    /** Declare what this card is about as an output of the program. */
    onsaveThis,
    onprintThis,
    /** `(name)` — a different one of this card's bindings was chosen. */
    onfocusbinding,
    /** The names this card's fragment declares, in the order it declares them.
     *
     * Passed in rather than parsed here: what a fragment declares is a question
     * about .imgql, and a component that draws a rectangle has no business
     * having an opinion about the language. */
    bindings = [],
    /** True while anything this card is about is queued or computing. */
    running = false,
    selected = false,
    /** True when this card is the one being shown alone. */
    focused = false,
    children,
  } = $props();

  /** The card's own menu. Right-clicking a card is about *this* card, which is
   * why it stops there and never reaches the board's "new card here". */
  const menu = $derived(
    [
      onmaximize && { label: "Maximize", hint: "double-click", onselect: onmaximize },
      onrename && { label: "Rename", hint: "double-click the name", onselect: startRename },
      onduplicate && { label: "Duplicate", hint: "mod+D", onselect: onduplicate },
      oncopy && { label: "Copy", hint: "mod+C", onselect: oncopy },
      oncut && { label: "Cut", hint: "mod+X", onselect: oncut },
      onderive &&
        card.kind === "code" && {
          label: "New result from this",
          hint: "mod+R",
          onselect: onderive,
        },
      ...(onlayover && layTargets.length
        ? [
            { separator: true },
            ...layTargets.map((target) => ({
              label: `Lay over “${target.title}”`,
              hint: "or alt-drag",
              onselect: () => onlayover(target.id),
            })),
          ]
        : []),
      onlens && { separator: true },
      ...(onlens
        ? [
            // The footer control sets every card at once, which is right for a
            // board you read at a glance and wrong for the one volume among
            // twenty programs. This is where a card says otherwise -- and
            // "follow the board" has to be here too, or an override is a
            // decision with no way back.
            { label: "Shows: follow the board", hint: "shift+mod+L", checked: !card.view,
              onselect: () => onlens("") },
            { label: "Shows: code", hint: "shift+mod+L", checked: card.view === "source",
              onselect: () => onlens("source") },
            { label: "Shows: code + value", hint: "shift+mod+L", checked: card.view === "both",
              onselect: () => onlens("both") },
            { label: "Shows: value", hint: "shift+mod+L", checked: card.view === "value",
              onselect: () => onlens("value") },
          ]
        : []),
      onsaveThis && { separator: true },
      onsaveThis && {
        label: "Save this",
        hint: "writes a save into the program",
        onselect: () => onsaveThis(),
      },
      onprintThis && {
        label: "Print this",
        hint: "writes a print into the program",
        onselect: () => onprintThis(),
      },
      onfocus && {
        label: focused ? "Leave focus" : "Focus",
        hint: focused ? "esc" : "long press",
        onselect: () => onfocus(!focused),
      },
      onsendtopage && { separator: true },
      onsendtopage && {
        label: "Send to next page",
        hint: "mod+→",
        onselect: () => onsendtopage(card.page + 1),
      },
      onsendtopage && {
        label: "Send to previous page",
        hint: "mod+←",
        disabled: card.page === 0,
        onselect: () => onsendtopage(card.page - 1),
      },
      onremove && { separator: true },
      onremove && {
        label: "Remove card",
        hint: "mod+Backspace",
        danger: true,
        onselect: onremove,
      },
    ].filter(Boolean),
  );

  /** The type a dragged card carries its ids in, set by `Bento`'s `dragOut`. */
  const IDS = "text/voxlogica-card-ids";

  /** True while a card is held over this one. */
  let landing = $state(false);

  let headerEl = $state(null);
  /** The title while it is being edited, or null. Double-clicking the title
   * renames; double-clicking the rest of the header maximizes. The two never
   * collide because a card's name and a card's size are different targets. */
  let renaming = $state(null);
  let titleInput = $state(null);

  $effect(() => {
    if (renaming !== null && titleInput) {
      titleInput.focus();
      // The whole name selected, the way every file manager on every platform
      // starts a rename: the common case is replacing it, and anyone who meant
      // to edit it just clicks or presses an arrow to place the caret.
      titleInput.select();
    }
  });

  /** Holding a card is asking to see it on its own.
   *
   * A press that does not become a drag is a press that meant something else,
   * and on a board where dragging is the main verb it is the only gesture left
   * that costs nothing to offer. Half a second: long enough not to fire while
   * somebody is deciding, short enough not to feel broken.
   */
  let longPress = null;

  function startLongPress() {
    if (!onfocus) return;
    cancelLongPress();
    longPress = setTimeout(() => {
      longPress = null;
      onfocus(!focused);
    }, 500);
  }

  function cancelLongPress() {
    if (longPress !== null) {
      clearTimeout(longPress);
      longPress = null;
    }
  }

  function startRename() {
    if (onrename) renaming = card.title;
  }

  function endRename(keep) {
    const text = renaming?.trim();
    renaming = null;
    if (keep && text) onrename?.(text);
  }
  let content = $state(null);
  /** True while a pointer owns this card. The card does not follow the pointer
   * in pixels: it snaps, cell by cell, as the pointer crosses each half-way
   * mark. A card that glides freely and then jumps on release shows you a
   * position that was never real; a card that snaps is always standing exactly
   * where dropping it would leave it. */
  let dragging = $state(false);
  let gesture = null;

  /** The drawn rectangle: whatever the board says, in pixels. */
  const px = $derived({
    x: at.x * pitch,
    y: at.y * pitch,
    w: (at.w ?? size.w) * pitch - gutter,
    h: (at.h ?? size.h) * pitch - gutter,
  });

  /** The smallest a self-sizing card is allowed to be.
   *
   * Content measured at zero -- an empty card, a result with nothing in it yet
   * -- would otherwise ask for one cell, and a card too small to show its own
   * name is not a card anybody can use. Only auto cards get this floor: a size
   * somebody chose by hand is a size they meant.
   */
  const AUTO_FLOOR = { w: 4, h: 2 };

  function clamp(w, h) {
    // The file's own minimum wins over the content's: an author who wrote one
    // meant it. Otherwise the smallest size this card's content is usable at.
    const floor = floorOf?.(card);
    const minW = card.minW ?? floor?.w ?? 1;
    const minH = card.minH ?? floor?.h ?? 1;
    let cw = Math.min(Math.max(w, minW), card.maxW ?? cols, cols);
    let ch = Math.min(Math.max(h, minH), card.maxH ?? rows, rows);
    if (card.aspect) {
      // Width leads: it is the axis the eye compares across a row of cards.
      ch = Math.min(Math.max(Math.round(cw / card.aspect), minH), rows);
    }
    return [cw, ch];
  }

  // ------------------------------------------------------------- auto-sizing

  function autoSize() {
    if (!card.auto || !content || !pitch) return;
    // Written straight to the node, not through a reactive class: Svelte flushes
    // the DOM on a microtask, so a `measuring = true` on the line above would
    // still be pending when the rect is read, and what came back would be the
    // size of the box we are trying to compute. This is a measurement, not
    // rendering; it is put back before anything can paint.
    const style = content.getAttribute("style");
    Object.assign(content.style, {
      position: "absolute",
      visibility: "hidden",
      width: "max-content",
      height: "max-content",
      // The only bound while measuring is the card's own maximum width: a long
      // line has to wrap somewhere, and the widest the card may ever be is the
      // only honest place for it to do so.
      maxWidth: `${(card.maxW ?? cols) * pitch - gutter}px`,
    });
    const box = content.getBoundingClientRect();
    if (style === null) content.removeAttribute("style");
    else content.setAttribute("style", style);
    // The card is not only its content: the header takes cells too, and a card
    // sized to the content alone clips by exactly that much.
    const [w, h] = clamp(
      Math.max(AUTO_FLOOR.w, Math.ceil((box.width + gutter) / pitch)),
      Math.max(AUTO_FLOOR.h, Math.ceil((box.height + (headerEl?.offsetHeight ?? 0) + gutter) / pitch)),
    );
    if (w !== size.w || h !== size.h) onmeasure?.(w, h);
  }

  $effect(() => {
    if (!card.auto || !content || !pitch) return;
    autoSize();
    // Content that changes size later -- a result that arrives, a log that
    // grows -- re-sizes the card without anyone asking.
    const observer = new ResizeObserver(() => autoSize());
    observer.observe(content);
    return () => observer.disconnect();
  });

  // ---------------------------------------------------------------- gestures

  /** Which edges a resize gesture is pulling. `move` pulls none. */
  const EDGES = {
    n: { top: 1 }, s: { bottom: 1 }, e: { right: 1 }, w: { left: 1 },
    ne: { top: 1, right: 1 }, nw: { top: 1, left: 1 },
    se: { bottom: 1, right: 1 }, sw: { bottom: 1, left: 1 },
  };

  function begin(event, mode) {
    if (event.button !== 0 || !pitch) return;
    event.preventDefault();
    try {
      // Capture keeps the gesture on this element when the pointer outruns the
      // card, which it always does on a fast drag. Not fatal if it is refused.
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* no active pointer with this id; the gesture still works, just leakier */
    }
    gesture = {
      mode,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      from: { x: at.x, y: at.y, w: size.w, h: size.h },
      rect: { x: at.x, y: at.y, w: size.w, h: size.h },
      edges: EDGES[mode] ?? {},
    };
    dragging = true;
    onpreview?.(gesture.rect, at_(event));
  }

  function move(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    const from = gesture.from;
    // Rounded, not floored: the card changes cell when the pointer passes the
    // half-way mark, which is where the eye already thinks it moved.
    if (gesture.mode === "move") {
      gesture.rect = {
        ...from,
        x: Math.min(Math.max(from.x + Math.round(dx / pitch), 0), cols - from.w),
        y: Math.min(Math.max(from.y + Math.round(dy / pitch), 0), rows - from.h),
      };
    } else {
      // A resize is up to four independent pulls. Dragging a top or left edge
      // moves the card as it shrinks it, which is what "pulling that edge"
      // means -- the opposite edge is the one that must not move.
      const { edges } = gesture;
      const cx = Math.round(dx / pitch);
      const cy = Math.round(dy / pitch);
      let { x, y, w, h } = from;
      if (edges.right) w = from.w + cx;
      if (edges.left) {
        w = from.w - cx;
        x = from.x + cx;
      }
      if (edges.bottom) h = from.h + cy;
      if (edges.top) {
        h = from.h - cy;
        y = from.y + cy;
      }
      const [cw, ch] = clamp(w, h);
      // Clamping may have refused the size; the moving edge then stays put
      // rather than sliding the card sideways for free.
      if (edges.left) x = from.x + from.w - cw;
      if (edges.top) y = from.y + from.h - ch;
      gesture.rect = {
        x: Math.max(x, 0),
        y: Math.max(y, 0),
        w: Math.min(cw, cols - Math.max(x, 0)),
        h: Math.min(ch, rows - Math.max(y, 0)),
      };
    }
    onpreview?.(gesture.rect, at_(event));
  }

  /** Where the pointer is, and what is held. The board needs the point to say
   * which card a drop would land *on*, which cells cannot answer: a card being
   * dragged covers several, and the one being aimed at is the one under the
   * cursor. */
  const at_ = (event) => ({
    clientX: event.clientX,
    clientY: event.clientY,
    alt: event.altKey,
  });

  function end(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const { rect, from } = gesture;
    gesture = null;
    dragging = false;
    // Committed before the preview is dropped, not after: the arrangement the
    // board is holding -- who moved aside, and to where -- exists only while the
    // gesture does. Clearing first threw it away and left the dragged card on
    // top of cards that had politely stepped out of its way.
    if (rect.x !== from.x || rect.y !== from.y || rect.w !== from.w || rect.h !== from.h) {
      movedAt = Date.now();
      // Where the pointer was, and what was held, travel *with* the release:
      // the key is up and the pointer is gone by the time anything downstream
      // could ask. The board needs the point because the card under the cursor
      // is the target -- "the only card overlapped" was measured wrong, since a
      // four-cell card dropped on a grid of four-cell cards covers two of them
      // and the intent is plainly the one being pointed at.
      oncommit?.(rect, {
        alt: event.altKey,
        clientX: event.clientX,
        clientY: event.clientY,
      });
    } else if (!event.shiftKey && !event.metaKey && !event.ctrlKey) {
      // A press that never became a drag was a click, and clicking one of
      // several selected cards means "just this one" -- otherwise a selection
      // could only ever grow.
      onselect?.(false, true);
    }
    onpreview?.(null);
  }

  /** A gesture the pointer can no longer finish, abandoned rather than kept.
   *
   * `end` insists on a pointerup from the pointer that began -- rightly, that is
   * what commits. But an engine that stops delivering pointer events mid-drag
   * (WebKit, once anything native takes the pointer) or a window that loses
   * focus leaves a gesture nothing can ever finish, and a board holding one
   * refuses all further input: the freeze this exists to make impossible.
   * Nothing is committed -- a gesture nobody could complete is not an intent. */
  /** When this card last actually moved or grew under the pointer.
   *
   * A double click is two clicks, and on this board a click is the tail of a
   * *drag*: press, move, release. Pick a card up, put it down, pick it up again
   * within the double-click window and the browser reports a dblclick nobody
   * performed -- so the card maximized itself, seemingly at random, which is
   * exactly what it was reported as.
   */
  let movedAt = 0;

  /** Maximize only when neither click was really a drag.
   *
   * The window is the platform's own double-click interval rather than a
   * guess at one: the two events this has to tell apart are exactly the two
   * the platform used to decide they were a pair.
   */
  function maybeMaximize() {
    if (Date.now() - movedAt < 600) return;
    onmaximize?.();
  }

  function abandon() {
    if (!gesture) return;
    gesture = null;
    dragging = false;
    onpreview?.(null);
  }

  /** The same gestures from the keyboard, which is the only way some people have
   * of arranging anything at all. */
  function onKeydown(event) {
    // F2 renames, everywhere that has ever had a rename. Enter belongs to the
    // card's content, so it cannot also mean this.
    if (event.key === "F2" && onrename) {
      event.preventDefault();
      startRename();
      return;
    }

    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[
      event.key
    ];
    if (!step) return;
    event.preventDefault();
    const [dx, dy] = step;
    if (event.shiftKey) {
      const [w, h] = clamp(size.w + dx, size.h + dy);
      oncommit?.({ x: at.x, y: at.y, w, h });
      return;
    }
    oncommit?.({
      x: Math.min(Math.max(at.x + dx, 0), cols - size.w),
      y: Math.min(Math.max(at.y + dy, 0), rows - size.h),
      w: size.w,
      h: size.h,
    });
  }
</script>

<!-- Focus leaving the window and a native drag ending are the two moments a
     pointer gesture can have been orphaned; both abandon it (see `abandon`). -->
<svelte:window onblur={abandon} ondragend={abandon} />

<ContextMenu label="{card.title} actions" items={menu}>
  <article
    data-card-id={card.id}
    class="card"
    class:running
    class:dragging
    class:laying
    class:invalid
    class:focused
    class:selected
    style="transform: translate3d({px.x}px, {px.y}px, 0); width: {px.w}px; height: {px.h}px;"
    aria-label={card.title}
  >
    <!-- The header is the drag handle, and it is focusable: a card you can only
         move with a pointer is a card some people cannot move. Double-click
         fills whatever room the card has around it. -->
    <header
      bind:this={headerEl}
      role="button"
      tabindex="0"
      aria-label="{card.title} — move with arrows, resize with shift+arrows"
      onpointerdown={(event) => {
        onselect?.(event.shiftKey || event.metaKey || event.ctrlKey);
        startLongPress(event);
        begin(event, "move");
      }}
      onpointermove={(event) => {
        cancelLongPress();
        move(event);
      }}
      onpointerup={(event) => {
        cancelLongPress();
        end(event);
      }}
      onpointercancel={(event) => {
        cancelLongPress();
        end(event);
      }}
      ondblclick={maybeMaximize}
      onkeydown={onKeydown}
    >
      {#if renaming !== null}
        <!-- svelte-ignore a11y_autofocus -->
        <input
          bind:this={titleInput}
          class="title rename"
          bind:value={renaming}
          aria-label="Card name"
          spellcheck="false"
          autocomplete="off"
          onpointerdown={(event) => event.stopPropagation()}
          ondblclick={(event) => event.stopPropagation()}
          onblur={() => endRename(true)}
          onkeydown={(event) => {
            // The field owns the keyboard while it is open: the board's own
            // chords -- and the browser's, for undo -- belong to the text.
            event.stopPropagation();
            if (event.key === "Enter" || event.key === "Tab") {
              // Tab keeps it too. A name abandoned because somebody tabbed away
              // is a name they will have to type again.
              endRename(true);
            } else if (event.key === "Escape") {
              endRename(false);
            }
          }}
        />
      {:else}
        <span
          class="title"
          role="presentation"
          ondblclick={(event) => {
            event.stopPropagation();
            startRename();
          }}
        >
          {card.title}
        </span>
      {/if}

      {#if bindings.length > 1 && onfocusbinding}
        <!-- Only when there is a choice to make. A fragment declaring one name
             is a fragment about that name, and a select with one option is a
             control that teaches nothing and costs a glance on every card. -->
        <select
          class="binding"
          aria-label="What {card.title} is about"
          value={card.focus ?? bindings[bindings.length - 1]}
          onpointerdown={(event) => event.stopPropagation()}
          ondblclick={(event) => event.stopPropagation()}
          onclick={(event) => event.stopPropagation()}
          onchange={(event) => onfocusbinding?.(event.currentTarget.value)}
        >
          {#each bindings as name (name)}
            <option value={name}>{name}</option>
          {/each}
        </select>
      {/if}

      {#if onrun}
        <!-- Run belongs to the card, not to the window: a run is a demand for
             what *this* card is about. Dependencies come along because they
             must, which is the engine's business and not something anyone
             should have to say. -->
        <button
          type="button"
          class="run"
          class:working={running}
          aria-label="Compute {card.title}"
          title="Compute this card"
          onpointerdown={(event) => event.stopPropagation()}
          ondblclick={(event) => event.stopPropagation()}
          onclick={(event) => {
            event.stopPropagation();
            onrun?.();
          }}
        >
          <!-- A triangle, drawn rather than typed: a glyph would inherit the
               text metrics and sit off-centre at every size but one. -->
          <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
            <path d="M3.5 2.2 L9.6 6 L3.5 9.8 Z" />
          </svg>
        </button>
      {/if}
    </header>

    <!-- The header arranges, the body travels -- and the markup says so:
         `draggable` sits on the body alone, never on the card. It was on the
         card once, guarded by the header's `preventDefault` on pointerdown, and
         that guard is a Chrome fact rather than a web fact: WebKit starts a
         draggable ancestor's native drag regardless, and the moment it does it
         stops delivering pointermove and pointerup -- so the arrange gesture
         never ended and the board froze, precisely in the window that is the
         application (WKWebView). Only `preventDefault` inside *dragstart* is
         honoured everywhere, and the belt below uses it: a drag that begins
         while an arrange gesture is live is refused, whatever engine let it
         start. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="body"
      class:landing={landing}
      ondragover={(event) => {
        if (!ondropcards || !event.dataTransfer) return;
        if (!event.dataTransfer.types.includes(IDS)) return;
        event.preventDefault();
        landing = true;
      }}
      ondragleave={() => (landing = false)}
      ondrop={(event) => {
        landing = false;
        const carried = event.dataTransfer?.getData(IDS);
        if (!carried) return;
        event.preventDefault();
        event.stopPropagation();
        ondropcards?.(carried.split("\n").filter(Boolean));
      }}
      draggable={ondragout ? "true" : undefined}
      ondragstart={(event) => {
        // A press on something you can *operate* is never "take this card out
        // of the board". `draggable` is inherited from the nearest draggable
        // ancestor, so the body was starting a card drag from inside a slider:
        // the thumb never moved, and a ghost with the card's name on it flew off
        // instead. Marking the row undraggable did nothing, because the body was
        // always the source.
        const from = event.target;
        if (from?.closest?.(".layers")) {
          // The rows own one drag of their own -- reordering, from the grip.
          // Anything else in there is a control, and controls are not handles.
          if (!from.closest("[data-grip]")) event.preventDefault();
          return;
        }
        if (from?.closest?.("input, button, select, textarea, a, label")) {
          event.preventDefault();
          return;
        }
        if (gesture !== null) {
          event.preventDefault();
          return;
        }
        ondragout?.(event);
      }}
      onpointerdown={(event) => onselect?.(event.shiftKey || event.metaKey || event.ctrlKey)}
    >
      <div bind:this={content} class="content">
        {@render children?.()}
      </div>
    </div>

    <!-- Eight invisible pulls: four edges and four corners. Nothing is drawn --
         the cursor is the affordance, which is how every window on this desktop
         has behaved for thirty years, and a card with a visible grip in the
         corner is a card wearing its implementation on the outside. -->
    {#if !focused}
      {#each ["n", "s", "e", "w", "ne", "nw", "se", "sw"] as edge (edge)}
        <span
          class="pull {edge}"
          role="presentation"
          onpointerdown={(event) => begin(event, edge)}
          onpointermove={move}
          onpointerup={end}
          onpointercancel={end}
        ></span>
      {/each}
    {/if}
  </article>
</ContextMenu>

<style>
  .card {
    position: absolute;
    top: 0;
    left: 0;
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    /* No border, no divider, no grip. A card is a surface that sits slightly
     * above the board, and that is the whole visual idea: outlines around
     * things that are already separated by space is the drawing of boxes for
     * their own sake. Depth and quiet type do the work instead. */
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
    transition:
      transform var(--motion-fast) var(--easing-standard),
      width var(--motion-fast) var(--easing-standard),
      height var(--motion-fast) var(--easing-standard),
      box-shadow var(--motion-fast) var(--easing-standard);
  }

  .card:hover {
    box-shadow: var(--shadow-md);
  }

  .card.dragging {
    /* No easing on the card being dragged: it snaps to the cell the pointer is
     * over, and interpolating that reads as lag. */
    transition: none;
    box-shadow: var(--shadow-overlay);
    z-index: var(--layer-overlay);
  }

  .card.focused {
    box-shadow: var(--shadow-overlay);
  }

  /* The card you are working with: a border, and only a border. An inset
   * shadow rather than an outline, because an outline is painted outside the
   * rounded corner and reads as a stray rule across the top of the card. */
  .card.selected {
    box-shadow: inset 0 0 0 var(--ring-width) var(--color-accent), var(--shadow-md);
  }

  .card.selected.dragging {
    box-shadow: inset 0 0 0 var(--ring-width) var(--color-accent), var(--shadow-overlay);
  }

  .card.invalid {
    /* The one place a line is worth drawing: a refusal has to be unmistakable,
     * and it lasts only as long as the pointer holds the card there. */
    outline: var(--border-width) solid var(--color-danger);
    outline-offset: calc(var(--border-width) * -1);
  }

  @media (prefers-reduced-motion: reduce) {
    .card {
      transition: none;
    }
  }

  header {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3) var(--space-1);
    cursor: grab;
    touch-action: none; /* the pointer belongs to the drag, not to scrolling */
    user-select: none;
  }

  .card.dragging header {
    cursor: grabbing;
  }

  /* Present but quiet, and pushed to the far end by the title's own growth.
   * A run button that announced itself on every card would make a board of
   * twenty cards look like a control panel. */
  /* Reads as the card's own subtitle, not as a form control: no chrome until
     it is approached, and the name it shows is the thing the eye is looking
     for. A select styled like a select would put a widget on every card. */
  .binding {
    flex: none;
    max-width: 40%;
    margin-left: auto;
    padding: 0 var(--space-1);
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-muted);
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    cursor: pointer;
    appearance: none;
    text-overflow: ellipsis;
  }

  .binding:hover,
  .binding:focus-visible {
    color: var(--color-text);
    background: var(--color-surface-sunken);
  }

  /* With a binding beside it, the run button no longer needs to push itself
     to the far end -- the select is already there. */
  .binding ~ .run {
    margin-left: 0;
  }

  .run {
    flex: none;
    display: grid;
    place-items: center;
    width: 1.25rem;
    height: 1.25rem;
    margin-left: auto;
    padding: 0;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-subtle);
    cursor: pointer;
    opacity: 0;
    transition:
      opacity var(--motion-fast) var(--easing-standard),
      color var(--motion-fast) var(--easing-standard);
  }

  .run svg {
    width: 0.75rem;
    height: 0.75rem;
    fill: currentColor;
  }

  /* Revealed by approach, and by the keyboard -- which is the part that is
   * usually forgotten and the reason `:focus-visible` is here at all. */
  .card:hover .run,
  .card.selected .run,
  .run:focus-visible {
    opacity: 1;
  }

  .run:hover {
    color: var(--color-text);
    background: var(--color-surface-sunken);
  }

  /* While it is working the button stops being a control and becomes the
   * card's own status: always visible, breathing on the accent. */
  .run.working {
    opacity: 1;
    color: var(--color-accent);
    animation: pulse var(--motion-pulse) var(--easing-standard) infinite;
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 0.45;
    }
    50% {
      opacity: 1;
    }
  }

  .rename {
    /* The field is the label, in the same place at the same size: renaming
     * should look like typing over the name, not like opening a dialogue. */
    min-width: 0;
    flex: 1;
    background: none;
    color: var(--color-text);
    outline: none;
  }

  .title {
    font-size: var(--text-2xs);
    font-weight: var(--weight-semibold);
    text-transform: uppercase;
    letter-spacing: var(--tracking-caps);
    color: var(--color-text-subtle);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* A drop in flight would become a layer of this card. Deliberately unlike the
   * displacement outline: one means "I am getting out of the way", the other
   * means "I am taking it in", and they must not look alike. */
  .laying {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  .laying::after {
    content: "lay over";
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    background: var(--color-accent);
    color: var(--color-surface);
    font-size: var(--text-2xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    pointer-events: none;
  }

  /* Something is about to land here. The card says so; the rows appear after. */
  .landing {
    box-shadow: inset 0 0 0 2px var(--color-accent);
    border-radius: var(--radius-sm);
  }

  .body {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }

  .content {
    /* The card's height, handed on. Every viewer asks for `height: 100%` -- a
     * volume fills its card, a result centres in it -- and a percentage height
     * against an auto-height parent is not a height at all: it computes to
     * `auto`, so a canvas fell back to its own drawing-buffer size and a card
     * pulled taller grew a band of empty board under a picture that never
     * followed. The one that grows instead of filling (the program, which
     * scrolls) says `min-height` rather than `height`, and still does. */
    height: 100%;
    padding: var(--space-1) var(--space-3) var(--space-3);
  }

  /* Invisible, and wide enough to hit without aiming. Corners sit above edges
   * so the diagonal wins where they meet. */
  .pull {
    position: absolute;
    touch-action: none;
  }

  .pull.n,
  .pull.s {
    left: 0;
    right: 0;
    height: var(--space-2);
    cursor: ns-resize;
  }

  .pull.e,
  .pull.w {
    top: 0;
    bottom: 0;
    width: var(--space-2);
    cursor: ew-resize;
  }

  .pull.n { top: 0; }
  .pull.s { bottom: 0; }
  .pull.e { right: 0; }
  .pull.w { left: 0; }

  .pull.ne,
  .pull.nw,
  .pull.se,
  .pull.sw {
    width: var(--space-4);
    height: var(--space-4);
    z-index: 1;
  }

  .pull.ne { top: 0; right: 0; cursor: nesw-resize; }
  .pull.nw { top: 0; left: 0; cursor: nwse-resize; }
  .pull.se { bottom: 0; right: 0; cursor: nwse-resize; }
  .pull.sw { bottom: 0; left: 0; cursor: nesw-resize; }
</style>
