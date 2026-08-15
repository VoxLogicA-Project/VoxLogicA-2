// Screenshots, taken by the only participant that has a screen.
//
// The server is asked "what does the workspace look like?" by agents that cannot
// see it, and it has no renderer of its own -- so it forwards the question to a
// connected tab and this answers it. Rendering the board server-side from the
// document would be a *reconstruction*: it would show what the layout says, not
// what the user is looking at, and the two differ exactly when it matters (a
// card whose content overflowed, a result that failed to render, a build error
// covering the page).
//
// No library. The DOM is serialised into an SVG <foreignObject> and drawn onto a
// canvas, which is what the screenshot libraries do underneath; the parts that
// make those libraries large -- walking every stylesheet of an unknown page,
// inlining webfonts, working around a decade of browser bugs -- do not apply to
// a page whose entire stylesheet we ship ourselves.

/** The element an MCP `target` names: the whole board, or one card. */
function locate(target: string | null): Element {
  if (target === null || target === "board") {
    // The lattice itself, not the page column around it: the board is as wide as
    // its cells and the column may be narrower, so photographing the column
    // would crop the right-hand cards out of the picture.
    return document.querySelector("main .board") ?? document.querySelector("main") ?? document.body;
  }
  if (target === "page") return document.querySelector("main") ?? document.body;
  const card = document.querySelector(`[data-card-id="${CSS.escape(target)}"]`);
  if (card === null) throw new Error(`no card on screen with id ${target}`);
  return card;
}

/** Every rule we ship, as one string: the page's own styles are the only ones
 * that can apply, since the bundle inlines all of its CSS. */
function styles(): string {
  const sheets = Array.from(document.styleSheets);
  const rules: string[] = [];
  for (const sheet of sheets) {
    try {
      for (const rule of Array.from(sheet.cssRules)) rules.push(rule.cssText);
    } catch {
      // A cross-origin sheet cannot be read. We ship none, so this is not ours.
    }
  }
  return rules.join("\n");
}

/** The size to photograph something at.
 *
 * A tab that is not rendering reports a zero-sized viewport, and everything
 * sized against the viewport -- the page column, `100dvh` -- measures zero with
 * it. What still has a size is content: the board is as wide as its own cells
 * whatever the window is doing. So the largest of the three answers is taken,
 * and a picture comes back from a background tab instead of a one-pixel strip.
 */
function measure(element: Element): { width: number; height: number } {
  const box = element.getBoundingClientRect();
  const scroll = element as HTMLElement;
  const board = document.querySelector("main .board");
  const fallback = board && board !== element ? board.getBoundingClientRect() : null;
  return {
    width: Math.max(1, Math.ceil(Math.max(box.width, scroll.scrollWidth || 0, fallback?.width ?? 0))),
    height: Math.max(
      1,
      Math.ceil(Math.max(box.height, scroll.scrollHeight || 0, fallback?.height ?? 0)),
    ),
  };
}

export async function capture(target: string | null): Promise<string> {
  const element = locate(target);
  const { width, height } = measure(element);

  const clone = element.cloneNode(true) as HTMLElement;
  // The clone is laid out at the origin of its own canvas, not at the position
  // it happened to have on the page -- and at the size it had *there*. A card is
  // a grid item, and `grid-column: 4 / span 5` means nothing once it is out of
  // its grid: without this the clone collapses to the height of its text and the
  // screenshot shows a card shorter than the one on screen.
  clone.style.margin = "0";
  clone.style.width = `${width}px`;
  clone.style.height = `${height}px`;
  clone.style.boxSizing = "border-box";

  const serialised = new XMLSerializer().serializeToString(clone);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
    `<style>${styles()}</style>` +
    `<foreignObject width="100%" height="100%">` +
    `<div xmlns="http://www.w3.org/1999/xhtml" style="width:${width}px;height:${height}px">` +
    serialised +
    `</div></foreignObject></svg>`;

  const image = new Image();
  // A data URL, not a blob URL: the SVG must be same-origin or the canvas is
  // tainted and `toDataURL` throws instead of returning the picture.
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  await image.decode();

  const scale = Math.min(window.devicePixelRatio || 1, 2);
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(width * scale);
  canvas.height = Math.ceil(height * scale);
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("no 2d context");
  // The page's own background, so a screenshot is not transparent where the
  // design system relies on the canvas colour.
  context.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.scale(scale, scale);
  context.drawImage(image, 0, 0);

  return canvas.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}
