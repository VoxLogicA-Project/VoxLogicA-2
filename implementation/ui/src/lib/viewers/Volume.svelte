<script>
  /**
   * An image or a volume, drawn by NiiVue, driven by props.
   *
   * NiiVue does WebGL, colormaps, orientation and crosshairs -- all of which is
   * a great deal of work nobody here should repeat. What this component owns is
   * the three things NiiVue cannot know about: a *declarative* surface, the
   * discipline about when its instance may be recreated, and the page's WebGL
   * budget.
   *
   * **Layers are a prop, and the prop is the truth.**
   *
   *     layers = [{ hash, url, colormap, opacity, visible }, …]
   *
   * Add, remove, reorder and restyle by changing the array; the component works
   * out the difference. The temptation is an imperative API mirroring NiiVue's
   * own -- `addVolume`, `setColormap` -- and it would put the truth in two
   * places, with the second one wrong within a week. The diff is what makes
   * reordering cheap: moving a layer must not reload a volume that is already
   * in the GPU, and reloading a 300 MB volume to change its order is the
   * difference between smooth and unusable.
   *
   * **The instance is recreated only when the viewer family changes**, never for
   * new data. That is the one idea worth keeping from the adapter this replaces
   * (`viewerAdapters.js`, removed in 57506e0): `adapterKey`. On WebGL the
   * distinction is not tidiness, it is whether the thing is usable.
   *
   * **A context is borrowed, not owned** (see contexts.js). A browser gives
   * about sixteen and a board can hold more cards than that; when this one loses
   * its lease it keeps its last frame as a bitmap and asks for the lease back on
   * approach. The blank canvas that a silently dropped context produces is
   * something nobody diagnoses from the symptom.
   */
  import { contexts } from "./contexts.js";

  let {
    /** `[{ hash, url, colormap?, opacity?, visible? }]`, back to front. */
    layers = [],
    /** "axial" | "coronal" | "sagittal" | "multi" — the family. Changing this
     * is the one thing that rebuilds the instance. */
    view = "multi",
    /** Told when the user moves the crosshair: `({ mm, voxel })`. */
    onlocate,
  } = $props();

  let host = $state(null);
  let canvas = $state(null);
  /** The last frame, kept while this viewer has no context. */
  let frozen = $state(null);
  let live = $state(false);
  let failure = $state(null);

  /** The NiiVue instance, and what it currently holds. Not `$state`: it is a
   * WebGL object graph, and making it reactive would mean proxying every
   * texture NiiVue owns. */
  let nv = null;
  let loaded = $state([]);
  let family = null;

  const SLICE = { axial: 0, coronal: 1, sagittal: 2, multi: 3 };

  /** The identity of a layer, for diffing. The hash *is* the identity -- two
   * layers with one hash are one volume, however they are styled. */
  const keyOf = (layer) => layer.hash ?? layer.url;

  /** Whoever holds a lease implements this; the pool calls it. */
  const holder = {
    release() {
      freeze();
      teardown();
      live = false;
    },
  };

  /** Keep what is on screen before the canvas stops being able to draw it.
   *
   * A viewer that went blank when its lease expired would look broken. It is
   * showing the last true thing it drew, which for a still image is the same
   * picture -- and the moment somebody approaches, it is live again.
   */
  function freeze() {
    try {
      if (canvas && canvas.width) frozen = canvas.toDataURL("image/png");
    } catch {
      /* a tainted or zero-sized canvas simply has no frame to keep */
    }
  }

  function teardown() {
    try {
      nv?.cleanup?.();
    } catch {
      /* going away regardless */
    }
    nv = null;
    loaded = [];
    family = null;
  }

  async function build() {
    const { Niivue } = await import("@niivue/niivue");
    nv = new Niivue({
      // Its own chrome, off: the card is the frame, and a second set of
      // borders inside the first is two answers to one question.
      isColorbar: false,
      isRadiologicalConvention: true,
      backColor: [0, 0, 0, 0],
      onLocationChange: (location) =>
        onlocate?.({ mm: location?.mm, voxel: location?.vox }),
    });
    nv.attachToCanvas(canvas);
    family = view;
    nv.setSliceType(SLICE[view] ?? SLICE.multi);
  }

  /** Make what NiiVue holds match the prop, moving as little as possible. */
  async function reconcile() {
    if (!nv) return;
    const wanted = layers.filter((layer) => keyOf(layer));

    // Gone: removed before anything is added, so the GPU never holds both the
    // old set and the new one at the same instant.
    for (const key of [...loaded]) {
      if (!wanted.some((layer) => keyOf(layer) === key)) {
        const at = loaded.indexOf(key);
        try {
          nv.removeVolumeByIndex(at);
        } catch {
          /* already gone */
        }
        loaded.splice(at, 1);
      }
    }

    // New: loaded in the order the prop gives, so a fresh viewer ends up
    // ordered without a second pass.
    for (const layer of wanted) {
      const key = keyOf(layer);
      if (loaded.includes(key)) continue;
      await nv.addVolumeFromUrl({
        url: layer.url,
        colormap: layer.colormap ?? (loaded.length ? "warm" : "gray"),
        opacity: layer.opacity ?? 1,
      });
      loaded.push(key);
    }

    // Order and style, on what is already there. This is the part that must not
    // reload anything: reordering is the common gesture and a volume is large.
    wanted.forEach((layer, index) => {
      const at = loaded.indexOf(keyOf(layer));
      if (at < 0) return;
      if (at !== index) {
        try {
          nv.setVolumeIndex?.(nv.volumes[at], index);
        } catch {
          /* an older NiiVue orders by load; the array below keeps us honest */
        }
        loaded.splice(index, 0, ...loaded.splice(at, 1));
      }
      const volume = nv.volumes?.[index];
      if (!volume) return;
      if (layer.colormap && volume.colormap !== layer.colormap) {
        nv.setColormap(volume.id, layer.colormap);
      }
      const opacity = layer.visible === false ? 0 : (layer.opacity ?? 1);
      if (volume.opacity !== opacity) nv.setOpacity(index, opacity);
    });

    nv.drawScene?.();
    frozen = null;
  }

  /** Everything that has to happen when this viewer is being looked at. */
  async function wake() {
    if (!canvas) return;
    contexts.acquire(holder);
    try {
      // The family, and only the family, decides whether to rebuild.
      if (nv && family !== view) teardown();
      if (!nv) await build();
      await reconcile();
      live = true;
      failure = null;
    } catch (error) {
      failure = String(error);
      teardown();
      live = false;
    }
  }

  /** What this viewer would draw, as a string.
   *
   * `layers` is rebuilt by the caller on every render -- it is a lookup per
   * element, not a stored array -- so identity says nothing about whether the
   * *picture* changed. Waking on identity meant a NiiVue reconcile and a
   * `drawScene` for every volume card on every re-render, several times a
   * second while dragging, which is exactly as heavy as it sounds.
   *
   * Everything that can change what is on screen is in here and nothing else
   * is, so equal signatures really do mean an identical picture.
   */
  const signature = $derived(
    [
      view,
      ...layers.map(
        (layer) =>
          `${keyOf(layer)}|${layer.url ?? ""}|${layer.colormap ?? ""}|` +
          `${layer.opacity ?? 1}|${layer.visible === false ? 0 : 1}`,
      ),
    ].join("\n"),
  );

  // A change to the picture is a reconcile, and a change to the viewer family is
  // a rebuild inside it. A change to neither is nothing.
  $effect(() => {
    // Read it so this re-runs when the picture moves -- and only then.
    void signature;
    if (canvas) wake();
  });

  /** A card can change size without the window moving, and NiiVue only hears
   * about the window.
   *
   * Its drawing buffer is sized from the canvas's client box when it attaches
   * and on `window.resize`, so resizing a *card* left the buffer at the old
   * size: the slice went on being drawn for a canvas that no longer existed,
   * which is a volume in one corner with its orientation labels off the edge.
   * The element could not overflow -- the card clips -- but the picture inside
   * it was wrong, which looks the same and is worse.
   *
   * Observed on the host rather than the canvas: the canvas is what NiiVue
   * resizes, and observing the thing you are about to resize is how a
   * ResizeObserver loop starts. */
  $effect(() => {
    if (!host) return;
    const observer = new ResizeObserver(() => {
      if (!nv || !live || !canvas) return;
      // NiiVue's own `resizeListener` does this arithmetic and then draws, but
      // it reaches it through `requestAnimationFrame`, which a background tab
      // never delivers -- so a card resized while the window was not in front
      // came back with a buffer for the size it used to be. The canvas box is
      // ours (the CSS above makes it fill the card), so its buffer is ours to
      // keep in step: the same computation, synchronously, no frame required.
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(canvas.offsetWidth * ratio));
      const height = Math.max(1, Math.round(canvas.offsetHeight * ratio));
      if (canvas.width === width && canvas.height === height) return;
      canvas.width = width;
      canvas.height = height;
      try {
        nv.drawScene();
      } catch {
        /* mid-teardown, or no context to draw into: the next wake redraws */
      }
    });
    observer.observe(host);
    return () => observer.disconnect();
  });

  $effect(() => () => {
    contexts.drop(holder);
    teardown();
  });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  bind:this={host}
  class="volume"
  onpointerenter={() => (live ? contexts.touch(holder) : wake())}
>
  <!-- `data-drawn` is the viewer saying how many volumes it is actually
       holding. Not instrumentation: "there is a canvas" and "there is a volume
       on it" are different claims, and only the second one is the promise this
       component makes. It is the only way anything outside the GPU can tell
       them apart -- a WebGL drawing buffer reads back empty once the frame has
       been composited, so counting lit pixels answers the wrong question. -->
  <canvas bind:this={canvas} class:hidden={!live} data-drawn={loaded.length}
  ></canvas>

  {#if !live && frozen}
    <!-- The last true frame. Not a placeholder: it is what this viewer drew,
         and hovering makes it live again. -->
    <img src={frozen} alt="" />
  {:else if !live && !failure}
    <p class="waiting">Hover to show</p>
  {/if}

  {#if failure}
    <p class="failure">{failure}</p>
  {/if}
</div>

<style>
  .volume {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 0;
    background: var(--color-surface-sunken);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }

  canvas,
  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .hidden {
    visibility: hidden;
  }

  .waiting,
  .failure {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    margin: 0;
    padding: var(--space-2);
    font-size: var(--text-2xs);
    text-align: center;
  }

  .waiting {
    color: var(--color-text-subtle);
  }

  .failure {
    color: var(--color-danger);
    font-family: var(--font-mono);
  }
</style>
