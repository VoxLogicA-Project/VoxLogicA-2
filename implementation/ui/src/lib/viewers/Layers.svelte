<script>
  /**
   * The layers of a stack: one row per picture, front-most last.
   *
   * Everything here writes a *comment* -- `style="gray@1.00, red@0.45!off"` on
   * the card's directive -- and never the expression. That is the whole reason
   * these controls can be free: the expression is the cache key, so an opacity
   * inside it would mean dragging a slider changes a hash, and a changed hash
   * recomputes the volume. Appearance has to live outside the program.
   *
   * **The state that matters is the one nobody thinks of.** A layer can be
   * switched off, and a layer can be *not there* -- this case never had a mask,
   * or that volume is still computing. Drawn alike, they produce the bug where
   * you switch a layer off, walk to a case without it, switch it back on, see
   * nothing happen, and go looking for a fault in your program. So absent is
   * drawn as absent: no switch, because there is nothing to switch.
   *
   * **Only the grip is draggable.** The whole row was, and then a press on the
   * slider began a native drag instead of moving the thumb -- so the one control
   * people reach for most could not be used at all. A row that is draggable
   * everywhere is a row whose contents are not usable anywhere.
   */
  import { card as cardActions } from "../actions/index.ts";
  import { RAMPS, swatchOf } from "./colormaps.js";

  let {
    card,
    layers,
    /** `(at, opacity)` while a slider is under the finger.
     *
     * The picture has to follow the thumb, and the picture is drawn from the
     * card's directive -- a round trip away. So the value goes two ways: up to
     * whoever assembles the layers, at once and for free, and into the document
     * once, when the finger lifts. Sixty writes a second would be sixty rewrites
     * of the program. */
    onlive,
  } = $props();
  /** Which row's palette is open, by index. */
  let picking = $state(null);
  /** Which row is being dragged, and which boundary it is over. */
  let held = $state(null);
  let over = $state(null);

  /** What the thumb shows: whatever the layer says, which already carries the
   * live value while there is one. One source, so the thumb and the picture
   * cannot disagree -- and no snap-back on release, because nothing local is
   * dropped at a moment when the document has not caught up yet. */
  const opacityOf = (layer) => layer.opacity;

  /** Drawn, switched off, or not there at all. */
  function stateOf(layer) {
    if (!layer.url) return layer.state === "done" ? "absent" : layer.state;
    return layer.visible ? "on" : "off";
  }

  const WORDS = {
    absent: "not in this case",
    running: "computing",
    unknown: "not computed",
    failed: "failed",
  };

  function drop(at) {
    if (held !== null && held !== at) cardActions.moveLayer(card.id, held, at);
    held = null;
    over = null;
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="layers" onpointerdown={(event) => event.stopPropagation()}>
  {#each layers as layer, at (layer.expression)}
    {@const state = stateOf(layer)}
    {@const there = state === "on" || state === "off"}
    <div
      class="row"
      class:off={state === "off"}
      class:gone={!there}
      class:held={held === at}
      class:over={over === at}
      ondragover={(event) => {
        if (held === null) return;
        event.preventDefault();
        over = at;
      }}
      ondragleave={() => {
        if (over === at) over = null;
      }}
      ondrop={(event) => {
        event.preventDefault();
        drop(at);
      }}
    >
      <!-- The only draggable part, so every control beside it still works. -->
      <span
        class="grip"
        data-grip
        draggable="true"
        title="drag to reorder"
        ondragstart={(event) => {
          held = at;
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", layer.expression);
        }}
        ondragend={() => {
          held = null;
          over = null;
        }}
      >
        <svg viewBox="0 0 6 10" aria-hidden="true"
          ><circle cx="1.5" cy="2" r="1" /><circle cx="4.5" cy="2" r="1" /><circle
            cx="1.5"
            cy="5"
            r="1"
          /><circle cx="4.5" cy="5" r="1" /><circle cx="1.5" cy="8" r="1" /><circle
            cx="4.5"
            cy="8"
            r="1"
          /></svg
        >
      </span>

      {#if there}
        <button
          class="eye"
          class:shut={!layer.visible}
          title={layer.visible ? "hide this layer" : "show this layer"}
          onclick={() => cardActions.setLayerStyle(card.id, at, { on: !layer.visible })}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8Z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
            />
            <circle cx="8" cy="8" r="2.1" fill="currentColor" />
            {#if !layer.visible}
              <path d="M2.5 13.5 13.5 2.5" stroke="currentColor" stroke-width="1.3" />
            {/if}
          </svg>
        </button>
      {:else}
        <span class="eye empty" aria-hidden="true"></span>
      {/if}

      <!-- The colormap as the ramp it is. A single dot cannot say what a
           colormap does; the gradient is the thing itself. -->
      <button
        class="ramp"
        style:background={swatchOf(layer.colormap)}
        title={layer.colormap ?? "gray"}
        disabled={!there}
        onclick={() => (picking = picking === at ? null : at)}
      ></button>

      <span class="name" title={layer.expression}>{layer.expression}</span>

      {#if there}
        <input
          class="opacity"
          type="range"
          min="0"
          max="100"
          value={Math.round(opacityOf(layer) * 100)}
          title="opacity"
          oninput={(event) => onlive?.(at, event.currentTarget.value / 100)}
          onchange={(event) =>
            cardActions.setLayerStyle(card.id, at, {
              opacity: event.currentTarget.value / 100,
            })}
        />
        <span class="pct">{Math.round(opacityOf(layer) * 100)}</span>
      {:else}
        <span class="why">{WORDS[state] ?? state}</span>
      {/if}

      {#if layers.length > 1}
        <!-- Out of the stack and onto the board, wearing the colour it had. The
             other half of laying a card over another, and it has to be exactly
             the other half or neither is a gesture anybody trusts. -->
        <button
          class="out"
          title="take this layer out as its own card"
          onclick={() => cardActions.splitLayer(card.id, at)}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M6 10 10.5 5.5M10.5 5.5H7M10.5 5.5V9"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
              stroke-linecap="round"
            />
            <rect
              x="2.5"
              y="2.5"
              width="11"
              height="11"
              rx="2"
              fill="none"
              stroke="currentColor"
              stroke-width="1.1"
              opacity="0.45"
            />
          </svg>
        </button>
      {/if}
    </div>

    {#if picking === at}
      <div class="palette">
        {#each RAMPS as ramp (ramp.name)}
          <button
            class="chip"
            class:on={(layer.colormap ?? "gray") === ramp.name}
            style:background={ramp.css}
            title={ramp.label}
            onclick={() => {
              picking = null;
              cardActions.setLayerStyle(card.id, at, { colormap: ramp.name });
            }}
          ></button>
        {/each}
      </div>
    {/if}
  {/each}
</div>

<style>
  .layers {
    flex: none;
    display: flex;
    flex-direction: column;
    gap: 0;
    margin-top: var(--space-1);
    padding: var(--space-1);
    border-radius: var(--radius-sm);
    background: var(--color-surface-sunken);
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1);
    border-radius: var(--radius-sm);
    font-size: var(--text-2xs);
    transition: background var(--motion-fast) var(--easing-standard);
  }

  .row:hover {
    background: var(--color-surface-hover);
  }

  .held {
    opacity: 0.35;
  }

  /* Where it would land: the boundary, because that is what a reorder crosses. */
  .over {
    box-shadow: inset 0 2px 0 var(--color-accent);
  }

  .off {
    color: var(--color-text-subtle);
  }

  /* Absent: the affordance is gone rather than broken. */
  .gone {
    color: var(--color-text-subtle);
    opacity: 0.6;
  }

  .grip {
    flex: none;
    display: grid;
    place-items: center;
    width: var(--space-3);
    cursor: grab;
    color: var(--color-text-subtle);
    opacity: 0;
    transition: opacity var(--motion-fast) var(--easing-standard);
  }

  .row:hover .grip {
    opacity: 1;
  }

  .grip svg {
    width: 6px;
    fill: currentColor;
  }

  .eye,
  .out {
    flex: none;
    display: grid;
    place-items: center;
    width: var(--space-4);
    height: var(--space-4);
    padding: 0;
    border: 0;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-subtle);
    cursor: pointer;
    transition:
      color var(--motion-fast) var(--easing-standard),
      background var(--motion-fast) var(--easing-standard);
  }

  .eye svg,
  .out svg {
    width: 13px;
    height: 13px;
  }

  .eye:hover,
  .out:hover {
    color: var(--color-text);
    background: var(--color-surface-active);
  }

  .eye:not(.shut) {
    color: var(--color-text);
  }

  .eye.empty {
    cursor: default;
  }

  /* The colormap drawn as itself. */
  .ramp {
    flex: none;
    width: var(--space-5);
    height: var(--space-3);
    padding: 0;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: transform var(--motion-fast) var(--easing-standard);
  }

  .ramp:hover:not(:disabled) {
    transform: scale(1.08);
  }

  .ramp:disabled {
    cursor: default;
    opacity: 0.3;
  }

  .name {
    flex: 1;
    min-width: 0;
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .why {
    font-style: italic;
    color: var(--color-text-subtle);
    white-space: nowrap;
  }

  .opacity {
    flex: none;
    width: var(--space-8);
    margin: 0;
    accent-color: var(--color-accent);
  }

  .pct {
    flex: none;
    width: var(--space-4);
    text-align: right;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--color-text-subtle);
  }

  .palette {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-1) var(--space-2) var(--space-6);
  }

  .chip {
    width: var(--space-6);
    height: var(--space-3);
    padding: 0;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: transform var(--motion-fast) var(--easing-standard);
  }

  .chip:hover {
    transform: scale(1.08);
  }

  .chip.on {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 1px var(--color-accent);
  }
</style>
