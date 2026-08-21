// Read the real colormaps out of the package and emit the module the picker
// uses. The ramp a chip shows is then the ramp the GPU draws -- not a guess.
import { readFileSync, readdirSync, writeFileSync } from "node:fs";

const DIR = "node_modules/@niivue/niivue/src/cmaps";

/** The curated set, grouped the way somebody looking for one would look. */
const GROUPS = [
  ["Grayscale", ["gray", "bone", "cividis", "copper", "surface"]],
  ["Heat", ["hot", "hotiron", "warm", "inferno", "magma", "plasma"]],
  ["Perceptual", ["viridis", "turbo", "mako", "rocket", "batlow", "cubehelix"]],
  ["Clinical", ["ct_brain", "ct_bones", "ct_soft", "ct_vessels", "ct_head", "nih"]],
  ["Categorical", ["jet", "actc", "freesurfer", "x_rain", "linspecer", "hsv"]],
];

/** Sample a NiiVue cmap into CSS stops. `I` is the position, 0..255. */
function gradient(cmap) {
  const { R, G, B, I } = cmap;
  const stops = I.map((i, n) => {
    const at = ((i / 255) * 100).toFixed(1);
    return `rgb(${R[n]} ${G[n]} ${B[n]}) ${at}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

const out = [];
for (const [group, names] of GROUPS) {
  for (const name of names) {
    let text;
    try {
      text = readFileSync(`${DIR}/${name}.json`, "utf8");
    } catch {
      console.error(`missing: ${name}`);
      continue;
    }
    out.push({ name, group, css: gradient(JSON.parse(text)) });
  }
}
console.log(JSON.stringify(out, null, 2));
console.error(`${out.length} of ${readdirSync(DIR).length - 1} shipped colormaps`);
