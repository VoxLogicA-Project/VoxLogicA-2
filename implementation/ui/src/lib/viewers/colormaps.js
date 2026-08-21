// The colormaps a layer can wear, drawn as themselves.
//
// Every `name` here is a colormap NiiVue actually ships -- checked against the
// package rather than guessed, because a name it does not know is a layer that
// silently keeps the colour it had. The `css` beside it is an *approximation of
// the same ramp*, for the swatch: the GPU renders the real thing, and a control
// that has to show a gradient in 40 pixels of CSS cannot ask the GPU for one.
//
// Why ramps and not dots. A single dot says "red"; it cannot say whether the
// low end is black or white, and for a scan laid under a mask that is the whole
// question -- a ramp that starts white erases everything beneath it. The
// gradient is the thing itself, so the choice is made by looking rather than by
// remembering what a word did last time.
//
// The order is the order they are offered in, and it is deliberate: the greys a
// scan is drawn with first, then the saturated ramps that read as an overlay on
// top of one, then the perceptual maps for a quantity rather than a mask.

/** @type {{name: string, label: string, css: string}[]} */
export const RAMPS = [
  {
    name: "gray",
    label: "Gray — what a scan is",
    css: "linear-gradient(90deg, #000, #fff)",
  },
  {
    name: "bone",
    label: "Bone — grey, cooled",
    css: "linear-gradient(90deg, #000, #3d4a5c, #a3b0bd, #fff)",
  },
  {
    name: "red",
    label: "Red — a mask over a scan",
    css: "linear-gradient(90deg, #000, #7f0000, #e11d1d, #ff8a7a)",
  },
  {
    name: "green",
    label: "Green — a second mask",
    css: "linear-gradient(90deg, #000, #04471c, #1faa4b, #9df2b4)",
  },
  {
    name: "blue",
    label: "Blue — ground truth, by convention",
    css: "linear-gradient(90deg, #000, #0b2f7a, #2f6fe0, #a9c8ff)",
  },
  {
    name: "warm",
    label: "Warm — amber through white",
    css: "linear-gradient(90deg, #000, #7a3d00, #e08a00, #ffe6a3)",
  },
  {
    name: "winter",
    label: "Winter — blue through green",
    css: "linear-gradient(90deg, #0000ff, #0080a0, #00b06b, #00ff80)",
  },
  {
    name: "inferno",
    label: "Inferno — perceptual, for a quantity",
    css: "linear-gradient(90deg, #000004, #420a68, #932667, #dd513a, #fca50a, #fcffa4)",
  },
];

const BY_NAME = new Map(RAMPS.map((ramp) => [ramp.name, ramp]));

/** The ramp to paint on a swatch for a colormap, named or not. */
export function swatchOf(name) {
  return (BY_NAME.get(name ?? "gray") ?? BY_NAME.get("gray")).css;
}

/** Is this a colormap the viewer will actually honour? */
export function known(name) {
  return BY_NAME.has(name);
}
