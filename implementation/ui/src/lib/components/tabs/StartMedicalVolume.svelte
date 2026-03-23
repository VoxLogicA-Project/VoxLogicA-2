<script>
  import StartViewerHost from "$lib/components/tabs/StartViewerHost.svelte";
  import { buildMedicalViewerContract } from "$lib/components/tabs/viewers/viewerContracts.js";

  export let niftiUrl = "";
  export let layers = [];
  export let label = "value";

  $: contract = buildMedicalViewerContract({ niftiUrl, layers, label });
</script>

<StartViewerHost {contract} />
