const tokenPattern = /[A-Za-z0-9_.$+\-*/<>=!?~^%:&|]/;

export const extractTokenAt = (text, position) => {
  if (!text || !Number.isInteger(position) || position < 0) return "";
  const safePos = Math.max(0, Math.min(position, text.length));
  let start = safePos;
  let end = safePos;
  while (start > 0 && tokenPattern.test(text[start - 1])) start -= 1;
  while (end < text.length && tokenPattern.test(text[end])) end += 1;
  return text.slice(start, end).trim().replace(/^[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+|[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+$/g, "");
};

export const extractTokenInfoAt = (text, position) => {
  if (!text || !Number.isInteger(position) || position < 0) return { token: "", start: 0, end: 0 };
  const safePos = Math.max(0, Math.min(position, text.length));
  let start = safePos;
  let end = safePos;
  while (start > 0 && tokenPattern.test(text[start - 1])) start -= 1;
  while (end < text.length && tokenPattern.test(text[end])) end += 1;
  const token = text.slice(start, end).trim().replace(/^[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+|[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+$/g, "");
  return { token, start, end };
};

export const isOperatorToken = (token) => {
  if (!token) return false;
  return !/[A-Za-z0-9_$]/.test(token);
};

export const expressionContextAt = (text, position) => {
  const safePos = Math.max(0, Math.min(Number(position || 0), text.length));
  let start = safePos;
  let end = safePos;
  while (start > 0 && text[start - 1] !== "\n") start -= 1;
  while (end < text.length && text[end] !== "\n") end += 1;
  const line = text.slice(start, end).trim();
  if (!line) return "";
  return line.length <= 140 ? line : `${line.slice(0, 137)}...`;
};

export const textIndexFromPoint = (textarea, clientX, clientY) => {
  const text = textarea.value || "";
  const rect = textarea.getBoundingClientRect();
  if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) return null;

  const style = window.getComputedStyle(textarea);
  const font = style.font || `${style.fontSize} ${style.fontFamily}`;
  const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.5;
  const padLeft = Number.parseFloat(style.paddingLeft) || 0;
  const padTop = Number.parseFloat(style.paddingTop) || 0;

  const probe = document.createElement("span");
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.whiteSpace = "pre";
  probe.style.font = font;
  probe.textContent = "MMMMMMMMMM";
  document.body.appendChild(probe);
  const charWidth = Math.max(1, probe.getBoundingClientRect().width / 10);
  probe.remove();

  const x = clientX - rect.left + textarea.scrollLeft - padLeft;
  const y = clientY - rect.top + textarea.scrollTop - padTop;
  const lineIndex = Math.max(0, Math.floor(y / lineHeight));
  const colIndex = Math.max(0, Math.floor(x / charWidth));

  const lines = text.split("\n");
  const boundedLine = Math.min(lineIndex, Math.max(0, lines.length - 1));

  let offset = 0;
  for (let idx = 0; idx < boundedLine; idx += 1) {
    offset += lines[idx].length + 1;
  }
  offset += Math.min(colIndex, lines[boundedLine].length);

  return Math.min(offset, text.length);
};

export const renderTokenOverlayHtml = (text, resolveSymbolNode, sanitizeText, sanitizeAttr) => {
  if (!text) return "";

  const out = [];
  let idx = 0;
  while (idx < text.length) {
    const ch = text[idx];
    if (/[A-Za-z_.$]/.test(ch)) {
      let end = idx + 1;
      while (end < text.length && /[A-Za-z0-9_.$]/.test(text[end])) end += 1;
      const token = text.slice(idx, end);
      if (resolveSymbolNode(token)) {
        const encoded = encodeURIComponent(token);
        out.push(`<button type=\"button\" class=\"editor-token-hit\" data-token=\"${sanitizeAttr(encoded)}\" tabindex=\"-1\" title=\"Inspect ${sanitizeAttr(token)}\">${sanitizeText(token)}</button>`);
      } else {
        out.push(sanitizeText(token));
      }
      idx = end;
      continue;
    }
    out.push(sanitizeText(ch));
    idx += 1;
  }

  return out.join("");
};
