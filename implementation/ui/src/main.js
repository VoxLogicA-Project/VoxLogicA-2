/** Browser entry point.
 *
 * `__DEV__` is replaced by a literal at build time (see build.mjs), so the
 * `import()` below is unreachable in a shipped bundle and esbuild removes the
 * dev page, its five sections and all of their CSS. The gallery is a
 * development instrument, and paying for it in production would be the first
 * step towards it drifting out of date.
 */
import { mount } from "svelte";

import "./app.css";
import App from "./App.svelte";
import { connect } from "./lib/connection.js";

connect();

mount(App, { target: document.getElementById("app") });

if (__DEV__) {
  // Failing to mount the panel must never take the application with it: the
  // panel is for looking at the design system, and the app is the thing the
  // user actually came for.
  import("./lib/gallery/dev.js")
    .then(({ mountDevPanel }) => mountDevPanel())
    .catch((error) => console.error("the dev page failed to mount", error));
}
