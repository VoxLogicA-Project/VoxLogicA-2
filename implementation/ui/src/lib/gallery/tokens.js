/** Reads the design tokens back out of the live stylesheet.
 *
 * Not from a hand-kept manifest: the palette and type pages must show what the
 * app is actually using, and a list maintained next to tokens.css would be the
 * one thing in this system that could quietly disagree with it. Walking the
 * CSSOM means adding a token makes it appear, renaming one makes it move, and
 * deleting one makes it vanish -- with no second edit anywhere.
 */

/** Custom property names declared on `:root`, in declaration order. */
export function rootTokenNames() {
  const names = [];
  const seen = new Set();

  const collect = (rule) => {
    // Dark-mode overrides live in a media rule and redeclare :root; their names
    // are the same set, so recursing only matters for tokens that exist *only*
    // in a themed block.
    if (rule.media && rule.cssRules) {
      for (const nested of Array.from(rule.cssRules)) collect(nested);
      return;
    }
    if (rule.selectorText !== ":root" || !rule.style) return;
    for (const name of Array.from(rule.style)) {
      if (!name.startsWith("--") || seen.has(name)) continue;
      seen.add(name);
      names.push(name);
    }
  };

  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try {
      rules = sheet.cssRules;
    } catch {
      continue; // a cross-origin sheet; ours are same-origin
    }
    for (const rule of Array.from(rules ?? [])) collect(rule);
  }
  return names;
}

export function tokenValue(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Ordered groups, so the palette page reads as a story rather than a dump. */
const GROUPS = [
  {
    id: "primitives",
    title: "Primitives",
    note: "Raw ramps. A component may never name one of these — they exist so the roles below have somewhere to point.",
    kind: "color",
    match: (name) => /^--(gray|white|blue|red|green)/.test(name),
  },
  {
    id: "roles",
    title: "Colour roles",
    note: "The only colours a component is allowed to say. Dark mode redefines this tier and nothing else.",
    kind: "color",
    match: (name) => name.startsWith("--color-"),
  },
  {
    id: "space",
    title: "Space",
    note: "A 4px rhythm. Every gap, pad and inset in the UI is one of these.",
    kind: "space",
    match: (name) => name.startsWith("--space-"),
  },
  {
    id: "radius",
    title: "Radius",
    kind: "radius",
    match: (name) => name.startsWith("--radius-"),
  },
  {
    id: "elevation",
    title: "Elevation",
    note: "Almost invisible on purpose: depth is a border and a surface change; the shadow only lifts true overlays.",
    kind: "shadow",
    match: (name) => name.startsWith("--shadow-"),
  },
  {
    id: "motion",
    title: "Motion",
    note: "All three collapse to 0ms under prefers-reduced-motion.",
    kind: "raw",
    match: (name) => name.startsWith("--motion-") || name.startsWith("--easing-"),
  },
];

export function tokenGroups() {
  const names = rootTokenNames();
  const claimed = new Set();
  const groups = GROUPS.map((group) => {
    const tokens = names
      .filter((name) => !claimed.has(name) && group.match(name))
      .map((name) => {
        claimed.add(name);
        return { name, value: tokenValue(name) };
      });
    return { ...group, tokens };
  }).filter((group) => group.tokens.length > 0);
  return groups;
}

/** Type tokens, split by what they control. */
export function typeGroups() {
  const names = rootTokenNames();
  const pick = (prefix) =>
    names
      .filter((name) => name.startsWith(prefix))
      .map((name) => ({ name, value: tokenValue(name) }));
  return {
    families: pick("--font-"),
    sizes: pick("--text-"),
    leading: pick("--leading-"),
    tracking: pick("--tracking-"),
    weights: pick("--weight-"),
  };
}
