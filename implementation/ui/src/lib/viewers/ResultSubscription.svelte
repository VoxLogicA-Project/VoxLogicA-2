<script>
  /**
   * A subscription with a lifetime, expressed as a component.
   *
   * The server pushes result updates only for the nodes somebody is looking at,
   * so something has to say when the looking starts and stops. A component is
   * the honest place for that: "on screen" is exactly a component's lifetime,
   * and Svelte already runs the teardown when the card is closed, turned away
   * from, or scrolled onto another page. An effect on a list of visible cards
   * would be the same subscription with the bookkeeping done by hand.
   *
   * It renders nothing.
   */
  import { results } from "../store/results.svelte.ts";

  let { node = "" } = $props();

  // Re-runs when the card is rebound to a different name, and its cleanup
  // releases the old one -- which is the whole reason the store counts
  // references rather than holding a set.
  $effect(() => {
    const hash = results.hashFor(node);
    if (!hash) return;
    results.subscribe(hash);
    return () => results.unsubscribe(hash);
  });
</script>
