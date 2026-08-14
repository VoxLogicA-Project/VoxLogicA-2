<script>
  /**
   * A controller for the gallery, around the real board.
   *
   * `Bento` is a controlled component: it renders a layout and reports gestures,
   * and in the app the workspace replica is what turns a reported gesture into a
   * new layout. A specimen with nobody playing that part would be a board whose
   * cards do not move -- which would document the opposite of the truth.
   *
   * This is a controller, not a copy: the component under it is the one the app
   * mounts, so a specimen that misbehaves is a component that misbehaves.
   */
  import Bento from "./Bento.svelte";

  let { cards: seed = [], children, ...rest } = $props();

  let cards = $state(structuredClone(seed));
  let page = $state(0);

  const find = (id) => cards.find((card) => card.id === id);

  function move(id, x, y) {
    const card = find(id);
    if (card) {
      card.x = x;
      card.y = y;
    }
  }

  function resize(id, w, h) {
    const card = find(id);
    if (card) {
      card.w = w;
      card.h = h;
      // The app records this by writing w/h to the document; here the flag is
      // the whole record.
      card.auto = false;
    }
  }
</script>

<Bento
  {...rest}
  {cards}
  {page}
  onmove={move}
  onresize={resize}
  onpage={(next) => (page = next)}
>
  {#snippet children(card)}
    {@render children?.(card)}
  {/snippet}
</Bento>
