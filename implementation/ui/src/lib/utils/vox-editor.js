import { sanitizeAttr, sanitizeText } from "$lib/utils/sanitize.js";

export const VOX_KEYWORDS = [
  "import",
  "let",
  "in",
  "for",
  "do",
  "true",
  "false",
];

const KEYWORD_SET = new Set(VOX_KEYWORDS);
const IDENT_START = /[A-Za-z_.$]/;
const IDENT_CONT = /[A-Za-z0-9_.$]/;
const NUMBER_CHAR = /[0-9]/;
const COMPLETION_CHAR = /[A-Za-z0-9_.$]/;

export const parseDiagnosticLocation = (diag) => {
  const location = String(diag?.location || "");
  const message = String(diag?.message || "");
  const raw = `${location} ${message}`;

  let match = raw.match(/line\s+(\d+)(?:\s*,\s*column\s+(\d+))?/i);
  if (match) {
    return {
      line: Number(match[1]),
      column: match[2] ? Number(match[2]) : null,
    };
  }

  match = raw.match(/(?:^|\s)(\d+):(\d+)(?:\s|$)/);
  if (match) {
    return {
      line: Number(match[1]),
      column: Number(match[2]),
    };
  }

  return null;
};

const tokenizeLine = (line) => {
  const out = [];
  let idx = 0;

  while (idx < line.length) {
    const ch = line[idx];

    if (ch === "/" && line[idx + 1] === "/") {
      out.push({ kind: "comment", text: line.slice(idx) });
      break;
    }

    if (ch === '"') {
      let end = idx + 1;
      while (end < line.length) {
        const c = line[end];
        if (c === '"' && line[end - 1] !== "\\") {
          end += 1;
          break;
        }
        end += 1;
      }
      out.push({ kind: "string", text: line.slice(idx, end) });
      idx = end;
      continue;
    }

    if (NUMBER_CHAR.test(ch)) {
      let end = idx + 1;
      while (end < line.length && /[0-9.]/.test(line[end])) end += 1;
      out.push({ kind: "number", text: line.slice(idx, end) });
      idx = end;
      continue;
    }

    if (IDENT_START.test(ch)) {
      let end = idx + 1;
      while (end < line.length && IDENT_CONT.test(line[end])) end += 1;
      const token = line.slice(idx, end);
      out.push({ kind: KEYWORD_SET.has(token) ? "keyword" : "identifier", text: token });
      idx = end;
      continue;
    }

    if (/\s/.test(ch)) {
      let end = idx + 1;
      while (end < line.length && /\s/.test(line[end])) end += 1;
      out.push({ kind: "space", text: line.slice(idx, end) });
      idx = end;
      continue;
    }

    out.push({ kind: "operator", text: ch });
    idx += 1;
  }

  return out;
};

const renderToken = (token, symbolSet) => {
  const text = String(token.text || "");
  const safeText = sanitizeText(text);
  if (token.kind === "space") return safeText;
  if (token.kind === "identifier" && symbolSet.has(text)) {
    const encoded = encodeURIComponent(text);
    return (
      `<button type="button" class="vx-editor__symbol" ` +
      `data-token="${sanitizeAttr(encoded)}" title="Inspect ${sanitizeAttr(text)}">` +
      `${safeText}</button>`
    );
  }
  const className = `vx-editor__token vx-editor__token--${sanitizeAttr(token.kind)}`;
  return `<span class="${className}">${safeText}</span>`;
};

export const buildOverlayHtml = (text, symbols = {}, diagnostics = []) => {
  const source = String(text || "");
  const symbolSet = new Set(Object.keys(symbols || {}));
  const diagnosticLines = new Set();
  for (const diag of Array.isArray(diagnostics) ? diagnostics : []) {
    const loc = parseDiagnosticLocation(diag);
    if (loc?.line && Number.isFinite(loc.line) && loc.line > 0) {
      diagnosticLines.add(loc.line);
    }
  }

  const lines = source.split("\n");
  return lines
    .map((line, idx) => {
      const lineNo = idx + 1;
      const lineClass = diagnosticLines.has(lineNo) ? "vx-editor__line vx-editor__line--error" : "vx-editor__line";
      const tokens = tokenizeLine(line);
      const rendered = tokens.map((token) => renderToken(token, symbolSet)).join("");
      return `<span class="${lineClass}" data-line="${lineNo}">${rendered || " "}</span>`;
    })
    .join("");
};

export const expressionContextAt = (text, position) => {
  const source = String(text || "");
  const safePos = Math.max(0, Math.min(Number(position || 0), source.length));
  let start = safePos;
  let end = safePos;
  while (start > 0 && source[start - 1] !== "\n") start -= 1;
  while (end < source.length && source[end] !== "\n") end += 1;
  const line = source.slice(start, end).trim();
  if (!line) return "";
  return line.length <= 160 ? line : `${line.slice(0, 157)}...`;
};

export const extractTokenInfoAt = (text, position) => {
  const source = String(text || "");
  if (!Number.isInteger(position) || position < 0) return { token: "", start: 0, end: 0 };

  const safePos = Math.max(0, Math.min(position, source.length));
  let start = safePos;
  let end = safePos;
  while (start > 0 && COMPLETION_CHAR.test(source[start - 1])) start -= 1;
  while (end < source.length && COMPLETION_CHAR.test(source[end])) end += 1;
  const token = source.slice(start, end).trim();
  return { token, start, end };
};

export const isOperatorToken = (token) => !!token && !/[A-Za-z0-9_$]/.test(token);

export const textIndexFromPoint = (textarea, clientX, clientY) => {
  const text = String(textarea?.value || "");
  if (!textarea) return null;
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

const lineAndColumnAt = (text, position) => {
  const source = String(text || "");
  const safe = Math.max(0, Math.min(Number(position || 0), source.length));
  let line = 1;
  let lastLineBreak = -1;
  for (let i = 0; i < safe; i += 1) {
    if (source[i] === "\n") {
      line += 1;
      lastLineBreak = i;
    }
  }
  return { line, column: safe - lastLineBreak };
};

export const completionContextAt = (text, cursor) => {
  const source = String(text || "");
  const safeCursor = Math.max(0, Math.min(Number(cursor || 0), source.length));
  let from = safeCursor;
  while (from > 0 && COMPLETION_CHAR.test(source[from - 1])) from -= 1;
  let to = safeCursor;
  while (to < source.length && COMPLETION_CHAR.test(source[to])) to += 1;
  const prefix = source.slice(from, safeCursor);
  const token = source.slice(from, to);
  const lc = lineAndColumnAt(source, safeCursor);
  return {
    text: source,
    cursor: safeCursor,
    from,
    to,
    prefix,
    token,
    line: lc.line,
    column: lc.column,
  };
};

export const buildDefaultCompletions = (context, { symbols = {}, keywords = VOX_KEYWORDS, builtins = [] } = {}) => {
  const prefix = String(context?.prefix || "");
  const prefixLower = prefix.toLowerCase();
  const pool = new Map();

  for (const keyword of keywords) {
    pool.set(keyword, {
      label: keyword,
      insertText: keyword,
      kind: "keyword",
      detail: "language keyword",
    });
  }
  for (const builtin of builtins) {
    const label = String(builtin || "");
    if (!label) continue;
    pool.set(label, {
      label,
      insertText: label,
      kind: "primitive",
      detail: "known primitive",
    });
  }
  for (const symbol of Object.keys(symbols || {})) {
    pool.set(symbol, {
      label: symbol,
      insertText: symbol,
      kind: "symbol",
      detail: "declared symbol",
    });
  }

  return Array.from(pool.values())
    .filter((item) => {
      if (!prefix) return true;
      return String(item.label || "").toLowerCase().startsWith(prefixLower);
    })
    .sort((a, b) => {
      const aLabel = String(a.label || "");
      const bLabel = String(b.label || "");
      const aExact = aLabel.toLowerCase() === prefixLower;
      const bExact = bLabel.toLowerCase() === prefixLower;
      if (aExact && !bExact) return -1;
      if (!aExact && bExact) return 1;
      return aLabel.localeCompare(bLabel);
    });
};

export const applyCompletion = (text, context, item) => {
  const source = String(text || "");
  const insertText = String(item?.insertText || item?.label || "");
  const from = Math.max(0, Math.min(Number(context?.from || 0), source.length));
  const to = Math.max(from, Math.min(Number(context?.to || from), source.length));
  const nextText = `${source.slice(0, from)}${insertText}${source.slice(to)}`;
  const nextCursor = from + insertText.length;
  return { text: nextText, cursor: nextCursor };
};

export const getCaretCoordinates = (textarea, position) => {
  if (!textarea) return { left: 0, top: 0, height: 0 };
  const value = String(textarea.value || "");
  const safePos = Math.max(0, Math.min(Number(position || 0), value.length));
  const style = window.getComputedStyle(textarea);

  const mirror = document.createElement("div");
  mirror.style.position = "absolute";
  mirror.style.visibility = "hidden";
  mirror.style.whiteSpace = "pre-wrap";
  mirror.style.wordWrap = "break-word";
  mirror.style.overflow = "hidden";
  mirror.style.width = style.width;
  mirror.style.font = style.font;
  mirror.style.lineHeight = style.lineHeight;
  mirror.style.padding = style.padding;
  mirror.style.border = style.border;
  mirror.style.letterSpacing = style.letterSpacing;
  mirror.style.tabSize = style.tabSize;

  const before = value.slice(0, safePos);
  const after = value.slice(safePos) || ".";
  mirror.textContent = before;
  const marker = document.createElement("span");
  marker.textContent = after[0];
  mirror.appendChild(marker);
  document.body.appendChild(mirror);

  const markerRect = marker.getBoundingClientRect();
  const mirrorRect = mirror.getBoundingClientRect();
  const textareaRect = textarea.getBoundingClientRect();
  const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.5;

  const left = markerRect.left - mirrorRect.left - textarea.scrollLeft + textareaRect.left;
  const top = markerRect.top - mirrorRect.top - textarea.scrollTop + textareaRect.top;
  document.body.removeChild(mirror);

  return { left, top, height: lineHeight };
};
