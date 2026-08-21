// What a layer can be painted with: a colour, or a colormap.
//
// **Colours first, because most overlays are one.** A mask is a region, not a
// quantity -- "these voxels" -- and a ramp says something about magnitude that a
// mask does not have. NiiVue draws with colormaps, so a chosen colour becomes
// one: a single hue ramping up from transparent, which is the same shape as the
// single-hue maps the package itself ships (`red`, `violet`), so a colour and a
// built-in behave alike over a scan.
//
// **Colormaps second, and they are the real ones.** Every gradient below was
// generated from the package's own `src/cmaps/*.json` by sampling `I` for the
// stop and `R,G,B` for the colour -- so the chip shows the ramp the GPU will
// draw rather than somebody's impression of it. Twenty-nine of the seventy-two
// shipped, grouped the way somebody hunting for one would look: greys for a
// scan, heat for activation, perceptual for a quantity that must not lie, the CT
// window presets, and the categorical maps for labels.
//
// Regenerate with tools/colormaps.mjs after a NiiVue upgrade.

/** A classic swatch grid. Solid colours, named, and legible on black -- which
 * is what a volume is drawn on. */
export const COLOURS = [
  { name: "Red", hex: "#e5484d" },
  { name: "Crimson", hex: "#e93d82" },
  { name: "Pink", hex: "#d6409f" },
  { name: "Purple", hex: "#8e4ec6" },
  { name: "Violet", hex: "#6e56cf" },
  { name: "Indigo", hex: "#3e63dd" },
  { name: "Blue", hex: "#0090ff" },
  { name: "Cyan", hex: "#00a2c7" },
  { name: "Teal", hex: "#12a594" },
  { name: "Jade", hex: "#29a383" },
  { name: "Green", hex: "#30a46c" },
  { name: "Grass", hex: "#46a758" },
  { name: "Lime", hex: "#bdee63" },
  { name: "Yellow", hex: "#ffe629" },
  { name: "Amber", hex: "#ffc53d" },
  { name: "Orange", hex: "#f76b15" },
  { name: "Bronze", hex: "#a18072" },
  { name: "Gray", hex: "#b0b4ba" },
  { name: "White", hex: "#ffffff" },
];

/** @type {{name: string, group: string, css: string}[]} */
export const RAMPS = [
  { name: "gray", group: "Grayscale", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(255 255 255) 100.0%)" },
  { name: "bone", group: "Grayscale", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(103 126 165) 60.0%, rgb(255 255 255) 100.0%)" },
  { name: "cividis", group: "Grayscale", css: "linear-gradient(90deg, rgb(0 32 76) 0.0%, rgb(86 92 108) 25.1%, rgb(166 156 117) 75.3%, rgb(255 233 69) 100.0%)" },
  { name: "copper", group: "Grayscale", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(61 41 25) 20.0%, rgb(122 81 51) 40.0%, rgb(183 122 76) 60.0%, rgb(244 163 102) 80.0%, rgb(255 203 127) 100.0%)" },
  { name: "surface", group: "Grayscale", css: "linear-gradient(90deg, rgb(1 1 1) 0.0%, rgb(240 128 128) 60.0%, rgb(255 255 255) 100.0%)" },
  { name: "hot", group: "Heat", css: "linear-gradient(90deg, rgb(3 0 0) 0.0%, rgb(255 0 0) 37.3%, rgb(255 255 0) 74.9%, rgb(255 255 255) 100.0%)" },
  { name: "hotiron", group: "Heat", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(255 0 0) 50.2%, rgb(255 126 0) 74.9%, rgb(255 255 255) 100.0%)" },
  { name: "warm", group: "Heat", css: "linear-gradient(90deg, rgb(255 127 0) 0.0%, rgb(255 196 0) 50.2%, rgb(255 254 0) 100.0%)" },
  { name: "inferno", group: "Heat", css: "linear-gradient(90deg, rgb(0 0 4) 0.0%, rgb(120 28 109) 25.1%, rgb(237 105 37) 75.3%, rgb(240 249 33) 100.0%)" },
  { name: "magma", group: "Heat", css: "linear-gradient(90deg, rgb(0 0 4) 0.0%, rgb(148 44 128) 42.0%, rgb(183 55 121) 50.2%, rgb(223 74 104) 60.4%, rgb(247 112 92) 70.2%, rgb(252 253 191) 100.0%)" },
  { name: "plasma", group: "Heat", css: "linear-gradient(90deg, rgb(13 8 135) 0.0%, rgb(156 23 158) 25.1%, rgb(237 121 83) 75.3%, rgb(240 249 33) 100.0%)" },
  { name: "viridis", group: "Perceptual", css: "linear-gradient(90deg, rgb(68 1 84) 0.0%, rgb(49 104 142) 25.1%, rgb(53 183 121) 75.3%, rgb(253 231 37) 100.0%)" },
  { name: "turbo", group: "Perceptual", css: "linear-gradient(90deg, rgb(48 18 59) 0.0%, rgb(48 18 59) 0.4%, rgb(64 64 162) 6.3%, rgb(70 107 227) 12.5%, rgb(65 150 255) 19.2%, rgb(25 226 187) 32.5%, rgb(132 255 81) 46.3%, rgb(195 241 52) 54.9%, rgb(244 199 58) 64.3%, rgb(254 158 47) 71.0%, rgb(218 57 7) 85.9%, rgb(122 4 3) 100.0%)" },
  { name: "mako", group: "Perceptual", css: "linear-gradient(90deg, rgb(11 4 5) 0.0%, rgb(59 45 91) 22.0%, rgb(55 165 172) 65.5%, rgb(222 245 229) 100.0%)" },
  { name: "rocket", group: "Perceptual", css: "linear-gradient(90deg, rgb(3 5 26) 0.0%, rgb(112 31 87) 28.6%, rgb(144 29 91) 36.1%, rgb(188 22 86) 46.3%, rgb(236 76 62) 62.7%, rgb(246 158 117) 80.4%, rgb(255 250 235) 100.0%)" },
  { name: "batlow", group: "Perceptual", css: "linear-gradient(90deg, rgb(1 25 89) 0.0%, rgb(10 42 92) 4.3%, rgb(15 56 95) 8.6%, rgb(17 68 96) 12.9%, rgb(21 79 98) 17.3%, rgb(27 88 98) 21.6%, rgb(36 97 96) 25.9%, rgb(49 105 91) 30.2%, rgb(65 111 83) 34.5%, rgb(82 116 74) 38.8%, rgb(99 122 64) 43.1%, rgb(118 127 55) 47.5%, rgb(140 133 46) 52.2%, rgb(161 138 43) 56.5%, rgb(183 142 49) 60.8%, rgb(203 146 62) 65.1%, rgb(222 150 79) 69.4%, rgb(238 155 100) 73.7%, rgb(248 162 126) 78.0%, rgb(253 170 151) 82.4%, rgb(253 178 175) 86.7%, rgb(253 186 199) 91.0%, rgb(252 195 223) 95.3%, rgb(250 204 250) 100.0%)" },
  { name: "cubehelix", group: "Perceptual", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(13 5 14) 3.1%, rgb(21 11 30) 6.3%, rgb(26 20 46) 9.8%, rgb(27 31 61) 12.9%, rgb(25 44 71) 16.1%, rgb(22 58 77) 19.2%, rgb(21 72 78) 22.7%, rgb(22 86 75) 25.9%, rgb(28 99 68) 29.0%, rgb(39 109 60) 32.2%, rgb(54 116 52) 35.3%, rgb(75 120 48) 38.8%, rgb(98 122 47) 42.0%, rgb(124 122 53) 45.1%, rgb(148 122 65) 48.2%, rgb(171 121 83) 51.8%, rgb(189 121 105) 54.9%, rgb(202 124 131) 58.0%, rgb(210 129 157) 61.2%, rgb(213 137 183) 64.7%, rgb(211 147 205) 67.8%, rgb(206 161 222) 71.0%, rgb(200 175 235) 74.1%, rgb(195 190 241) 77.3%, rgb(193 205 243) 80.8%, rgb(195 218 242) 83.9%, rgb(201 229 240) 87.1%, rgb(211 238 239) 90.2%, rgb(225 245 240) 93.7%, rgb(240 251 245) 96.9%, rgb(255 255 255) 100.0%)" },
  { name: "ct_brain", group: "Clinical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(199 127 127) 48.6%, rgb(255 255 255) 100.0%)" },
  { name: "ct_bones", group: "Clinical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(0 0 0) 0.4%, rgb(113 109 101) 50.2%, rgb(255 250 245) 100.0%)" },
  { name: "ct_soft", group: "Clinical", css: "linear-gradient(90deg, rgb(0 154 179) 0.0%, rgb(0 154 179) 11.8%, rgb(0 154 179) 24.3%, rgb(0 154 179) 34.5%, rgb(0 0 0) 66.7%, rgb(255 0 0) 78.4%, rgb(255 254 0) 91.0%, rgb(255 255 255) 100.0%)" },
  { name: "ct_vessels", group: "Clinical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(255 128 128) 34.1%, rgb(255 255 255) 100.0%)" },
  { name: "ct_head", group: "Clinical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(241 156 130) 0.8%, rgb(241 156 130) 1.2%, rgb(248 222 169) 25.1%, rgb(248 222 169) 47.8%, rgb(178 36 24) 55.7%, rgb(178 36 24) 67.5%, rgb(232 51 37) 71.4%, rgb(255 255 255) 98.8%, rgb(255 255 255) 99.2%, rgb(255 255 255) 100.0%)" },
  { name: "nih", group: "Clinical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(85 0 170) 5.9%, rgb(0 0 85) 12.2%, rgb(0 0 255) 24.7%, rgb(0 85 255) 31.0%, rgb(0 170 170) 37.3%, rgb(0 255 170) 43.5%, rgb(0 255 0) 49.8%, rgb(85 255 85) 56.1%, rgb(255 255 0) 62.4%, rgb(255 85 0) 74.9%, rgb(255 0 0) 85.1%, rgb(172 0 0) 100.0%)" },
  { name: "jet", group: "Categorical", css: "linear-gradient(90deg, rgb(0 0 127) 0.0%, rgb(0 127 255) 24.7%, rgb(127 255 127) 50.2%, rgb(255 127 0) 75.3%, rgb(127 0 0) 100.0%)" },
  { name: "actc", group: "Categorical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(0 0 136) 25.1%, rgb(24 177 0) 50.2%, rgb(248 254 0) 61.2%, rgb(255 0 0) 100.0%)" },
  { name: "freesurfer", group: "Categorical", css: "linear-gradient(90deg, rgb(0 0 0) 0.0%, rgb(245 245 245) 0.8%, rgb(205 62 78) 1.2%, rgb(120 18 134) 1.6%, rgb(196 58 250) 2.0%, rgb(220 248 164) 2.7%, rgb(230 148 34) 3.1%, rgb(0 118 14) 3.9%, rgb(122 186 220) 4.3%, rgb(236 13 176) 4.7%, rgb(12 48 255) 5.1%, rgb(204 182 142) 5.5%, rgb(42 204 164) 5.9%, rgb(119 159 176) 6.3%, rgb(220 216 20) 6.7%, rgb(103 255 255) 7.1%, rgb(60 60 60) 9.4%, rgb(255 165 0) 10.2%, rgb(165 42 42) 11.0%, rgb(160 32 240) 11.8%, rgb(0 200 200) 12.2%, rgb(245 245 245) 16.1%, rgb(205 62 78) 16.5%, rgb(120 18 134) 16.9%, rgb(196 58 250) 17.3%, rgb(220 248 164) 18.0%, rgb(230 148 34) 18.4%, rgb(0 118 14) 19.2%, rgb(122 186 220) 19.6%, rgb(236 13 176) 20.0%, rgb(13 48 255) 20.4%, rgb(220 216 20) 20.8%, rgb(103 255 255) 21.2%, rgb(255 165 0) 22.7%, rgb(165 42 42) 23.5%, rgb(160 32 240) 24.3%, rgb(0 200 221) 24.7%, rgb(120 190 150) 28.2%, rgb(200 70 255) 30.2%, rgb(255 148 10) 30.6%, rgb(255 148 10) 31.0%, rgb(164 108 226) 31.4%, rgb(164 108 226) 31.8%, rgb(164 108 226) 32.2%, rgb(234 169 30) 33.3%, rgb(0 0 64) 98.4%, rgb(0 0 112) 98.8%, rgb(0 0 160) 99.2%, rgb(0 0 208) 99.6%, rgb(0 0 255) 100.0%)" },
  { name: "x_rain", group: "Categorical", css: "linear-gradient(90deg, rgb(3 0 0) 0.0%, rgb(64 0 32) 12.5%, rgb(0 0 48) 25.1%, rgb(0 255 56) 37.6%, rgb(255 255 64) 62.7%, rgb(255 192 96) 75.3%, rgb(255 3 128) 100.0%)" },
  { name: "linspecer", group: "Categorical", css: "linear-gradient(90deg, rgb(94 79 162) 0.0%, rgb(50 131 189) 9.0%, rgb(90 186 167) 18.0%, rgb(152 214 164) 27.5%, rgb(215 240 155) 36.5%, rgb(238 244 169) 45.5%, rgb(249 237 168) 54.5%, rgb(254 210 123) 63.5%, rgb(252 157 86) 72.5%, rgb(241 100 68) 82.0%, rgb(209 57 79) 91.0%, rgb(158 1 66) 100.0%)" },
  { name: "hsv", group: "Categorical", css: "linear-gradient(90deg, rgb(255 0 0) 0.0%, rgb(255 255 0) 16.9%, rgb(0 255 0) 33.3%, rgb(0 255 255) 50.2%, rgb(0 0 255) 66.7%, rgb(255 0 255) 83.5%, rgb(255 0 0) 100.0%)" },
];

/** The groups, in the order they are offered. */
export const GROUPS = [...new Set(RAMPS.map((ramp) => ramp.group))];

const BY_NAME = new Map(RAMPS.map((ramp) => [ramp.name, ramp]));

/** Is this a colour rather than a colormap? Colours are written as hex. */
export const isColour = (value) => typeof value === "string" && value.startsWith("#");

/** What to paint on a swatch for whatever a layer is wearing. */
export function swatchOf(value) {
  if (isColour(value)) return value;
  return (BY_NAME.get(value ?? "gray") ?? BY_NAME.get("gray")).css;
}

/** A readable name for it, for a tooltip. */
export function nameOf(value) {
  if (isColour(value)) {
    const known = COLOURS.find((colour) => colour.hex === value.toLowerCase());
    return known ? known.name : value;
  }
  return value ?? "gray";
}
