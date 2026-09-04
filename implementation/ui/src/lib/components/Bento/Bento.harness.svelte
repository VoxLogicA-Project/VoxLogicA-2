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
  let zoom = $state(1);
  let focus = $state(null);
  let selection = $state([]);

  const find = (id) => cards.find((card) => card.id === id);

  /** One gesture, one layout: the card that moved and everyone it displaced,
   * applied together exactly as the app applies them. */
  function arrange(placements) {
    for (const spot of placements) {
      const card = find(spot.id);
      if (!card) continue;
      if (spot.x !== undefined) card.x = spot.x;
      if (spot.y !== undefined) card.y = spot.y;
      if (spot.w !== undefined) {
        card.w = spot.w;
        // The app records this by writing w/h to the document; here the flag is
        // the whole record.
        card.auto = false;
      }
      if (spot.h !== undefined) card.h = spot.h;
    }
  }

  function remove(id) {
    cards = cards.filter((card) => card.id !== id);
  }

  let added = 0;

  function add(kind, x, y, w, h) {
    added += 1;
    cards.push({ id: `${kind}${added}`, title: kind, kind, x, y, w, h, page });
  }
</script>

<Bento
  {...rest}
  {cards}
  {page}
  {zoom}
  {focus}
  onarrange={arrange}
  onadd={add}
  onremove={remove}
  onpage={(next) => (page = next)}
  onzoom={(next) => (zoom = next)}
  onfocus={(id) => (focus = id)}
  onduplicate={(id, spot) => {
    const source = find(id);
    if (!source || !spot) return;
    added += 1;
    cards.push({ ...structuredClone($state.snapshot(source)), id: `${id}-copy${added}`, ...spot });
  }}
  onselect={(ids) => (selection = ids)}
  {selection}
  onrename={(id, title) => {
    const card = find(id);
    if (card) card.title = title;
  }}
  onsendtopage={(id, next) => {
    const card = find(id);
    if (card) card.page = Math.max(0, next);
  }}
>
  {#snippet children(card)}
    {@render children?.(card)}
  {/snippet}
</Bento>
