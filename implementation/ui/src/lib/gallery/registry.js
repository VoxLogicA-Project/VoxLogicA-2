/** The component library, as data.
 *
 * `virtual:gallery` is generated at build time from every `*.gallery.js` beside
 * a component (see build.mjs), so this list *is* the library -- not a
 * description of it. The gallery renders the real components from these
 * entries; there is no second copy to keep in step.
 */
import entries from "virtual:gallery";

function validate(entry, index) {
  const where = entry?.name ?? `entry #${index}`;
  for (const field of ["name", "summary", "component", "variants"]) {
    if (!entry?.[field]) throw new Error(`gallery ${where}: missing "${field}"`);
  }
  if (!Array.isArray(entry.variants) || entry.variants.length === 0) {
    throw new Error(`gallery ${where}: "variants" must be a non-empty array`);
  }
  for (const [i, variant] of entry.variants.entries()) {
    if (!variant.label) throw new Error(`gallery ${where}: variant #${i} has no label`);
  }
  return entry;
}

export const components = entries
  .map(validate)
  .sort((a, b) => a.name.localeCompare(b.name));
