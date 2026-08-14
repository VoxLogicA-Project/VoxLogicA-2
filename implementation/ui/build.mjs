// Bundles src/main.js into <outdir>/app.js + app.css.
//
// Invoked by the Python server (voxlogica/ui/bundler.py), never by a watcher of
// its own: the server owns the "did anything change?" question, because it is
// the one that has to answer an HTTP request with fresh bytes. This script's
// only job is one build, then exit -- a cold esbuild start is ~100ms, which is
// far below the human reload loop, and paying it per change is much simpler
// than keeping a long-lived esbuild context alive across the server's lifetime.
//
// On failure it prints esbuild's own formatted diagnostics to stderr and exits
// nonzero; the server turns that into an in-page overlay rather than a 500.

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import * as esbuild from "esbuild";
import sveltePlugin from "esbuild-svelte";

const here = dirname(fileURLToPath(import.meta.url));

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
}

const outdir = resolve(arg("outdir", resolve(here, "dist")));
const dev = process.argv.includes("--dev");

await mkdir(outdir, { recursive: true });

const result = await esbuild.build({
  entryPoints: [resolve(here, "src/main.js")],
  bundle: true,
  outdir,
  entryNames: "app",
  assetNames: "[name]-[hash]",
  format: "esm",
  target: ["es2022"],
  platform: "browser",
  splitting: false,
  // Svelte 5 ships its runtime as ESM with `browser`/`development` export
  // conditions; without them esbuild resolves the server-side build, whose
  // effects never touch the DOM.
  conditions: dev ? ["browser", "development"] : ["browser"],
  sourcemap: dev ? "inline" : false,
  minify: !dev,
  logLevel: "silent",
  metafile: true,
  plugins: [
    sveltePlugin({
      compilerOptions: { dev, css: "external" },
    }),
  ],
});

// The fingerprint the server caches on is computed from the source tree, but
// the *inputs* esbuild actually read are the ground truth (a file outside src/,
// or a newly added import, still invalidates correctly because the server
// hashes the whole tree -- this metafile is here for debugging which files a
// bundle came from).
await writeFile(
  resolve(outdir, "meta.json"),
  JSON.stringify({ inputs: Object.keys(result.metafile.inputs) }, null, 2),
);

if (result.warnings.length) {
  const text = await esbuild.formatMessages(result.warnings, { kind: "warning", color: false });
  process.stderr.write(text.join("\n"));
}
