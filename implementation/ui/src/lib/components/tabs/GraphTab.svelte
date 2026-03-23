<script>
  import { getPlaygroundGraph } from "$lib/api/client.js";
  import { readPersistedStartProgram } from "$lib/utils/ui-persistence.js";

  export let active = false;

  let graph = null;
  let loading = false;
  let errorText = "";
  let nodesByDepth = [];
  let nodeCount = 0;
  let edgeCount = 0;

  const shortId = (value) => {
    const text = String(value || "");
    if (text.length <= 16) return text;
    return `${text.slice(0, 8)}…${text.slice(-6)}`;
  };

  const computeLayout = (payload) => {
    const nodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
    nodeCount = nodes.length;
    edgeCount = nodes.reduce((sum, node) => sum + (Array.isArray(node.dependencies) ? node.dependencies.length : 0), 0);
    const byId = new Map(nodes.map((node) => [String(node.node_id || ""), node]));
    const depthCache = new Map();

    const visit = (nodeId, stack = new Set()) => {
      if (depthCache.has(nodeId)) return depthCache.get(nodeId);
      if (stack.has(nodeId)) return 0;
      const node = byId.get(nodeId);
      if (!node) return 0;
      stack.add(nodeId);
      const deps = Array.isArray(node.dependencies) ? node.dependencies : [];
      let depth = 0;
      for (const dep of deps) {
        depth = Math.max(depth, visit(String(dep || ""), stack) + 1);
      }
      stack.delete(nodeId);
      depthCache.set(nodeId, depth);
      return depth;
    };

    const groups = new Map();
    for (const node of nodes) {
      const depth = visit(String(node.node_id || ""));
      const bucket = groups.get(depth) || [];
      bucket.push(node);
      groups.set(depth, bucket);
    }
    nodesByDepth = [...groups.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([depth, items]) => ({
        depth,
        items: items.sort((left, right) => String(left.operator || "").localeCompare(String(right.operator || ""))),
      }));
  };

  const loadGraph = async () => {
    if (!active) return;
    loading = true;
    errorText = "";
    try {
      const program = readPersistedStartProgram("");
      const payload = await getPlaygroundGraph(program);
      graph = payload;
      computeLayout(payload);
    } catch (error) {
      graph = null;
      nodesByDepth = [];
      nodeCount = 0;
      edgeCount = 0;
      errorText = `Unable to load compute graph: ${error.message}`;
    } finally {
      loading = false;
    }
  };

  $: if (active) {
    void loadGraph();
  }
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-graph">
  <article class="card graph-shell">
    <header class="graph-head">
      <div>
        <h2>Compute Graph</h2>
        <p class="muted">Nodes show variables (if any), operator name, and node hash.</p>
      </div>
      <button class="btn btn-ghost btn-small" type="button" disabled={loading} on:click={() => void loadGraph()}>
        Refresh
      </button>
    </header>

    {#if loading}
      <p class="muted">Loading graph…</p>
    {:else if errorText}
      <div class="inline-error">{errorText}</div>
    {:else if !graph}
      <p class="muted">No graph data yet.</p>
    {:else}
      <div class="graph-meta">
        <span>{nodeCount} nodes</span>
        <span>{edgeCount} edges</span>
        <span>{String(graph?.program_hash || "").slice(0, 12)}</span>
      </div>

      <div class="graph-grid">
        {#each nodesByDepth as column}
          <section class="graph-column">
            <div class="graph-column-head">Depth {column.depth}</div>
            {#each column.items as node}
              <article class="graph-node">
                <div class="graph-node-title">
                  <span class="graph-node-op">{node.operator || node.kind || "node"}</span>
                  <span class="graph-node-hash">{shortId(node.node_id)}</span>
                </div>
                <div class="graph-node-meta">
                  <span class="graph-node-kind">{node.output_kind || "unknown"}</span>
                  <span class="graph-node-kind">{node.kind || "unknown"}</span>
                </div>
                <div class="graph-node-vars">
                  {#if Array.isArray(node.variables) && node.variables.length}
                    {node.variables.join(", ")}
                  {:else}
                    <span class="muted">no symbol</span>
                  {/if}
                </div>
                {#if Array.isArray(node.dependencies) && node.dependencies.length}
                  <div class="graph-node-deps">
                    deps: {node.dependencies.map(shortId).join(", ")}
                  </div>
                {/if}
              </article>
            {/each}
          </section>
        {/each}
      </div>
    {/if}
  </article>
</section>
