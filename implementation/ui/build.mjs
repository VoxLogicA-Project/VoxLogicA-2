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

import { copyFile, glob, mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import * as esbuild from "esbuild";
import sveltePlugin from "esbuild-svelte";

const here = dirname(fileURLToPath(import.meta.url));

/**
 * Resolves `virtual:gallery` to every `*.gallery.js` beside a component.
 *
 * The dev gallery is not a page someone maintains -- it is generated from the
 * components themselves, so a component cannot exist without appearing in it
 * and cannot appear in it with stale variants. A hand-written registry would be
 * one import away from silently drifting; this cannot drift, because the list
 * is derived at build time and `watchFiles` re-derives it when the set changes.
 */
function galleryRegistry() {
  return {
    name: "gallery-registry",
    setup(build) {
      build.onResolve({ filter: /^virtual:gallery$/ }, () => ({
        path: "virtual:gallery",
        namespace: "gallery",
      }));

      build.onLoad({ filter: /.*/, namespace: "gallery" }, async () => {
        const root = resolve(here, "src/lib/components");
        const found = [];
        for await (const entry of glob("**/*.gallery.js", { cwd: root })) {
          found.push(entry);
        }
        // Sorted so the bundle is byte-identical for identical sources: the
        // Python side caches builds by source fingerprint, and a registry whose
        // order depended on directory iteration would defeat that.
        found.sort();
        const paths = found.map((entry) => resolve(root, entry));
        const imports = paths
          .map((path, index) => `import entry${index} from ${JSON.stringify(path)};`)
          .join("\n");
        const list = paths.map((_, index) => `entry${index}`).join(", ");
        return {
          contents: `${imports}\nexport default [${list}];\n`,
          loader: "js",
          resolveDir: root,
          watchFiles: paths,
          watchDirs: [root],
        };
      });
    },
  };
}

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
  // The dev-only design gallery is behind `if (__DEV__)` around a dynamic
  // import. With this defined to `false` and minification on, esbuild drops the
  // branch and the gallery, its sections and their styles never reach a
  // production bundle -- the same source, two honest outputs.
  define: { __DEV__: String(dev) },
  plugins: [
    galleryRegistry(),
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
// The mark, copied rather than redrawn. It is the single source: the page links
// to it, the application window sets the Dock icon from it, and anything else
// that needs one reads the same bytes. An icon inlined into the HTML was how
// this drifted the first time -- two copies of one drawing, and only one of
// them ever got updated.
await copyFile(resolve(here, "icon.svg"), resolve(outdir, "icon.svg"));

await writeFile(
  resolve(outdir, "meta.json"),
  JSON.stringify({ inputs: Object.keys(result.metafile.inputs) }, null, 2),
);

if (result.warnings.length) {
  const text = await esbuild.formatMessages(result.warnings, { kind: "warning", color: false });
  process.stderr.write(text.join("\n"));
}
