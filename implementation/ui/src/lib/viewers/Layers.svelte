<script>
  /**
   * The rows of a stack: one per picture, in drawing order.
   *
   * Everything here writes a *comment* -- `style="gray@1.00, red@0.45!off"` on
   * the card's directive -- and never the expression. That is the whole reason
   * these four controls can be free: the expression is the cache key, so an
   * opacity inside it would mean dragging a slider changes a hash, and a changed
   * hash recomputes the volume. Appearance has to be outside the program.
   *
   * **The state that matters is the one nobody thinks of.** A layer can be
   * switched off, and a layer can be *not there* -- this case never had a mask,
   * or that volume is still computing. Those look the same if you are careless,
   * and then somebody switches a layer off, walks to a case that never had it,
   * switches it back on, sees nothing happen, and goes looking for a fault in
   * their program. So absent is drawn as absent: no switch, because there is
   * nothing to switch.
   */
  import { card as cardActions } from "../actions/index.ts";

  let { card, layers } = $props();

  /** Real NiiVue colormap names, in the order the swatch walks them. Grey is
   * what a scan is, so it is first; the rest are things laid over one. */
  const WHEEL = ["gray", "red", "blue", "green", "warm", "winter", "bone"];

  /** Live while the finger is down, written once when it lifts.
   * Sixty writes a second would be sixty rewrites of the document. */
  let sliding = $state({});

  const opacityOf = (layer, at) => sliding[at] ?? layer.opacity;

  /** What this row is: drawn, switched off, or not there at all. */
  function stateOf(layer) {
    if (!layer.url) return layer.state === "done" ? "absent" : layer.state;
    return layer.visible ? "on" : "off";
  }

  const WORDS = {
    absent: "assente",
    running: "in corso",
    unknown: "non calcolato",
    failed: "errore",
  };
</script>

<div class="layers">
  {#each layers as layer, at (layer.expression)}
    {@const state = stateOf(layer)}
    {@const there = state === "on" || state === "off"}
    <div class="row" class:off={state === "off"} class:gone={!there}>
      <!-- The switch, and only when there is something to switch. -->
      {#if there}
        <button
          class="eye"
          title={layer.visible ? "spegni" : "accendi"}
          onclick={() => cardActions.setLayerStyle(card.id, at, { on: !layer.visible })}
        >
          {layer.visible ? "◉" : "○"}
        </button>
      {:else}
        <span class="eye" aria-hidden="true">–</span>
      {/if}

      <button
        class="swatch"
        style:background={swatchOf(layer.colormap)}
        title={layer.colormap}
        disabled={!there}
        onclick={() =>
          cardActions.setLayerStyle(card.id, at, {
            colormap: WHEEL[(WHEEL.indexOf(layer.colormap) + 1) % WHEEL.length],
          })}
      ></button>

      <span class="name" title={layer.expression}>{layer.expression}</span>

      {#if there}
        <input
          type="range"
          min="0"
          max="100"
          value={Math.round(opacityOf(layer, at) * 100)}
          title="trasparenza"
          oninput={(event) => (sliding[at] = event.currentTarget.value / 100)}
          onchange={(event) => {
            const opacity = event.currentTarget.value / 100;
            delete sliding[at];
            cardActions.setLayerStyle(card.id, at, { opacity });
          }}
        />
      {:else}
        <span class="why">{WORDS[state] ?? state}</span>
      {/if}
    </div>
  {/each}
</div>

<script module>
  /** A dot the eye can read, for a colormap the GPU renders. Not the colormap
   * itself: one swatch cannot show a ramp, and a recognisable colour is what
   * the row needs. */
  const DOTS = {
    gray: "#9aa0a6",
    red: "#ff5f56",
    blue: "#4c8dff",
    green: "#39d353",
    warm: "#d29922",
    winter: "#4cc4ff",
    bone: "#d8d2c4",
  };
  export function swatchOf(name) {
    return DOTS[name] ?? "#8b949e";
  }
</script>

<style>
  .layers {
    flex: none;
    display: flex;
    flex-direction: column;
    gap: 0;
    padding-top: var(--space-1);
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 2px var(--space-1);
    border-radius: var(--radius-sm);
    font-size: var(--text-2xs);
  }

  .row:hover {
    background: var(--color-surface-raised);
  }

  .off {
    color: var(--color-text-subtle);
  }

  /* Absent: the affordance is gone rather than broken. Nothing to switch,
   * nothing to slide, and the reason said in words. */
  .gone {
    color: var(--color-text-subtle);
    opacity: 0.55;
  }

  .eye {
    width: 14px;
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-align: center;
    cursor: pointer;
  }

  .gone .eye {
    cursor: default;
  }

  .swatch {
    width: 11px;
    height: 11px;
    flex: none;
    padding: 0;
    border: 0;
    border-radius: var(--radius-sm);
    cursor: pointer;
  }

  .off .swatch,
  .gone .swatch {
    opacity: 0.25;
  }

  .swatch:disabled {
    cursor: default;
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
  }

  input[type="range"] {
    width: 58px;
    margin: 0;
  }
</style>
