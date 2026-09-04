/** Entry point for the dev-only dev page (design system + debug).
 *
 * Kept in its own module so `main.js` can reach it with a dynamic import inside
 * `if (__DEV__)`. That is what lets esbuild drop the page, its five sections
 * and every one of their styles from a production bundle, rather than shipping
 * a gallery nobody asked for.
 */
import { mount } from "svelte";

import DevPanel from "./DevPanel.svelte";

export function mountDevPanel() {
  const host = document.createElement("div");
  host.id = "voxlogica-dev-panel";
  document.body.appendChild(host);
  return mount(DevPanel, { target: host });
}
