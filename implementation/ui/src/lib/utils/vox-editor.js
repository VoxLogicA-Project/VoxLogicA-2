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
const NUMBER_TOKEN = /^\d+(?:\.\d+)?$/;
const COMPLETION_CHAR = /[A-Za-z0-9_.$]/;
const BLOCK_TAGS = new Set(["DIV", "P", "LI", "PRE"]);
const EMPTY_LINE_MARKER = "\u200b";

const numberDecimals = (tokenText) => {
  const source = String(tokenText || "");
  const dot = source.indexOf(".");
  return dot >= 0 ? source.length - dot - 1 : 0;
};

const dragGranularityLevel = (deltaX, granularityPx) => {
  const threshold = Math.max(1, Number(granularityPx || 0));
  const distance = Number(deltaX || 0);
  if (distance >= 0) return Math.floor(distance / threshold);
  return -Math.floor(Math.abs(distance) / threshold);
};

const roundToDecimals = (value, decimals) => {
  const safeDecimals = Math.max(0, Number(decimals || 0));
  const scale = 10 ** safeDecimals;
  const rounded = Math.round((Number(value || 0) + Number.EPSILON) * scale) / scale;
  return Object.is(rounded, -0) ? 0 : rounded;
};

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
        const current = line[end];
        if (current === '"' && line[end - 1] !== "\\") {
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
      out.push({
        kind: KEYWORD_SET.has(token) ? "keyword" : "identifier",
        text: token,
      });
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

const normalizeSymbolStatus = (status) => {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "cached" || normalized === "completed") return "computed";
  if (normalized === "pending" || normalized === "missing") return "queued";
  if (normalized === "killed") return "failed";
  if (["idle", "queued", "running", "persisting", "computed", "failed"].includes(normalized)) {
    return normalized;
  }
  return "idle";
};

const diagnosticLookup = (diagnostics) => {
  const lines = new Set();
  const titles = new Map();

  for (const diag of Array.isArray(diagnostics) ? diagnostics : []) {
    const location = parseDiagnosticLocation(diag);
    if (!location?.line || !Number.isFinite(location.line) || location.line <= 0) continue;
    lines.add(location.line);
    const message = String(diag?.message || "Static error").trim();
    const prefix = location.column ? `Line ${location.line}:${location.column}` : `Line ${location.line}`;
    const code = diag?.code ? ` [${String(diag.code)}]` : "";
    titles.set(location.line, `${prefix} - ${message}${code}`);
  }

  return { lines, titles };
};

export const buildEditorDocument = (
  text,
  symbols = {},
  diagnostics = [],
  symbolStatuses = {},
  selectedSymbols = [],
  symbolTypes = {},
) => {
  const source = String(text || "");
  const symbolSet = new Set(Object.keys(symbols || {}));
  const selectedSet = new Set((Array.isArray(selectedSymbols) ? selectedSymbols : []).map((value) => String(value || "")));
  const diagnosticsByLine = diagnosticLookup(diagnostics);

  let nextStart = 0;
  return source.split("\n").map((line, index) => {
    const lineNumber = index + 1;
    const tokens = [];
    let tokenStart = nextStart;

    for (const token of tokenizeLine(line)) {
      const start = tokenStart;
      const end = start + token.text.length;
      tokenStart = end;

      if (token.kind === "identifier" && symbolSet.has(token.text)) {
        const status = normalizeSymbolStatus(symbolStatuses?.[token.text]);
        const selected = selectedSet.has(token.text);
        const typeLabel = String(symbolTypes?.[token.text] || "").trim();
        tokens.push({
          kind: "symbol",
          tokenKind: "identifier",
          text: token.text,
          start,
          end,
          symbol: token.text,
          status,
          selected,
          title: typeLabel ? `${token.text} (${typeLabel})` : `Inspect ${token.text}`,
          className: `vx-editor__symbol vx-editor__symbol--${status}${selected ? " vx-editor__symbol--selected" : ""}`,
        });
        continue;
      }

      tokens.push({
        kind: token.kind,
        tokenKind: token.kind,
        text: token.text,
        start,
        end,
        className: `vx-editor__token vx-editor__token--${token.kind}`,
      });
    }

    const lineStart = nextStart;
    nextStart += line.length + 1;
    const hasError = diagnosticsByLine.lines.has(lineNumber);

    return {
      number: lineNumber,
      start: lineStart,
      text: line,
      title: diagnosticsByLine.titles.get(lineNumber) || "",
      className: hasError ? "vx-editor__line vx-editor__line--error" : "vx-editor__line",
      isEmpty: line.length === 0,
      tokens,
    };
  });
};

const mergeLineParts = (target, source) => {
  if (!source.length) return target;
  target[target.length - 1] += source[0];
  for (let index = 1; index < source.length; index += 1) {
    target.push(source[index]);
  }
  return target;
};

const isBlockNode = (node) => node?.nodeType === 1 && BLOCK_TAGS.has(node.nodeName);
const isBreakNode = (node) => node?.nodeType === 1 && node.nodeName === "BR";

const collectNodeLines = (node, isRoot = false) => {
  if (!node) return [""];

  if (node.nodeType === 3) {
    return [String(node.textContent || "").replaceAll(EMPTY_LINE_MARKER, "")];
  }

  if (isBreakNode(node)) {
    return ["", ""];
  }

  if (!isRoot && isBlockNode(node)) {
    if (!node.childNodes?.length) {
      return ["", ""];
    }
    if (node.childNodes.length === 1 && isBreakNode(node.firstChild)) {
      return ["", ""];
    }
  }

  const lines = [""];
  for (const child of Array.from(node.childNodes || [])) {
    mergeLineParts(lines, collectNodeLines(child, false));
  }

  if (!isRoot && isBlockNode(node)) {
    lines.push("");
  }
  return lines;
};

export const readEditableText = (root) => {
  if (!root) return "";
  const lines = collectNodeLines(root, true);
  if (lines.length > 1 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines.join("\n");
};

const normalizeDomOffset = (node, offset) => {
  const safeOffset = Number(offset || 0);
  if (!node) return 0;
  if (node.nodeType === 3) {
    return Math.max(0, Math.min(safeOffset, String(node.textContent || "").length));
  }
  return Math.max(0, Math.min(safeOffset, node.childNodes?.length || 0));
};

const domPositionToTextOffset = (root, container, offset) => {
  const doc = root?.ownerDocument;
  if (!root || !container || !doc?.createRange || !root.contains(container)) return 0;

  const range = doc.createRange();
  range.selectNodeContents(root);
  range.setEnd(container, normalizeDomOffset(container, offset));

  const fragment = range.cloneContents();
  const wrapper = doc.createElement("div");
  wrapper.appendChild(fragment);
  return readEditableText(wrapper).length;
};

export const selectionOffsetsWithin = (root) => {
  const doc = root?.ownerDocument;
  const selection = doc?.getSelection?.();
  if (!root || !selection || !selection.rangeCount) return null;
  if (!root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return null;

  const anchor = domPositionToTextOffset(root, selection.anchorNode, selection.anchorOffset);
  const focus = domPositionToTextOffset(root, selection.focusNode, selection.focusOffset);
  return {
    anchor,
    focus,
    start: Math.min(anchor, focus),
    end: Math.max(anchor, focus),
    collapsed: anchor === focus,
  };
};

const positionFromTextOffset = (root, text, offset) => {
  const source = String(text || "");
  const safeOffset = Math.max(0, Math.min(Number(offset || 0), source.length));
  const lines = source.split("\n");

  let remaining = safeOffset;
  let lineIndex = 0;
  for (; lineIndex < lines.length; lineIndex += 1) {
    const lineLength = lines[lineIndex].length;
    if (remaining <= lineLength || lineIndex === lines.length - 1) {
      break;
    }
    remaining -= lineLength + 1;
  }

  const lineEl = root?.querySelector?.(`[data-line="${lineIndex + 1}"]`);
  if (!lineEl) {
    return {
      node: root,
      offset: root?.childNodes?.length || 0,
    };
  }

  const doc = lineEl.ownerDocument;
  const nodeFilter = doc?.defaultView?.NodeFilter;
  const walker = doc?.createTreeWalker?.(
    lineEl,
    nodeFilter?.SHOW_TEXT ?? 4,
    {
      acceptNode: (node) => {
        const textValue = String(node.textContent || "");
        return textValue.length
          ? nodeFilter?.FILTER_ACCEPT ?? 1
          : nodeFilter?.FILTER_SKIP ?? 3;
      },
    },
  );

  let textNode = walker?.nextNode?.() ? walker.currentNode : null;
  let lastTextNode = textNode;
  while (textNode) {
    const textValue = String(textNode.textContent || "");
    if (remaining <= textValue.length) {
      return { node: textNode, offset: remaining };
    }
    remaining -= textValue.length;
    lastTextNode = textNode;
    textNode = walker.nextNode();
  }

  if (lastTextNode) {
    return {
      node: lastTextNode,
      offset: String(lastTextNode.textContent || "").length,
    };
  }

  return { node: lineEl, offset: 0 };
};

export const restoreSelectionWithin = (root, text, start, end = start) => {
  const doc = root?.ownerDocument;
  const selection = doc?.getSelection?.();
  if (!root || !doc?.createRange || !selection) return false;

  const startPos = positionFromTextOffset(root, text, start);
  const endPos = positionFromTextOffset(root, text, end);
  const range = doc.createRange();
  range.setStart(startPos.node, normalizeDomOffset(startPos.node, startPos.offset));
  range.setEnd(endPos.node, normalizeDomOffset(endPos.node, endPos.offset));
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
};

export const caretClientRectFromSelection = (root) => {
  const doc = root?.ownerDocument;
  const selection = doc?.getSelection?.();
  if (!root || !selection || !selection.rangeCount) return null;
  if (!root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return null;

  const range = selection.getRangeAt(0).cloneRange();
  range.collapse(false);

  const directRect = Array.from(range.getClientRects?.() || []).find((rect) => rect.width || rect.height) || range.getBoundingClientRect?.();
  if (directRect && (directRect.width || directRect.height)) {
    return {
      left: directRect.left,
      top: directRect.top,
      bottom: directRect.bottom,
      height: directRect.height,
    };
  }

  const marker = doc.createElement("span");
  marker.textContent = EMPTY_LINE_MARKER;
  const originalRange = selection.getRangeAt(0).cloneRange();

  try {
    range.insertNode(marker);
    const rect = marker.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      bottom: rect.bottom,
      height: rect.height,
    };
  } finally {
    marker.remove();
    selection.removeAllRanges();
    selection.addRange(originalRange);
  }
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

  return {
    token: source.slice(start, end).trim(),
    start,
    end,
  };
};

export const dragNumberToken = (
  tokenText,
  { deltaX = 0, deltaY = 0, pixelsPerStep = 6, granularityPx = 80 } = {},
) => {
  const source = String(tokenText || "").trim();
  if (!NUMBER_TOKEN.test(source)) return null;

  const initialValue = Number(source);
  if (!Number.isFinite(initialValue)) return null;

  const baseDecimals = numberDecimals(source);
  const granularityLevel = dragGranularityLevel(deltaX, granularityPx);
  const step = 10 ** (granularityLevel - baseDecimals);
  const steps = Math.round((-Number(deltaY || 0)) / Math.max(1, Number(pixelsPerStep || 0)));
  const renderDecimals = Math.max(baseDecimals, baseDecimals - granularityLevel);
  const nextValue = roundToDecimals(initialValue + (steps * step), renderDecimals);

  return {
    value: nextValue,
    text: nextValue.toFixed(renderDecimals),
    steps,
    step,
    granularityLevel,
    renderDecimals,
  };
};

export const isOperatorToken = (token) => !!token && !/[A-Za-z0-9_$]/.test(token);

const lineAndColumnAt = (text, position) => {
  const source = String(text || "");
  const safe = Math.max(0, Math.min(Number(position || 0), source.length));
  let line = 1;
  let lastLineBreak = -1;

  for (let index = 0; index < safe; index += 1) {
    if (source[index] === "\n") {
      line += 1;
      lastLineBreak = index;
    }
  }

  return { line, column: safe - lastLineBreak };
};

export const completionContextAt = (text, cursor) => {
  const source = String(text || "");
  const safeCursor = Math.max(0, Math.min(Number(cursor || 0), source.length));
  let from = safeCursor;
  let to = safeCursor;

  while (from > 0 && COMPLETION_CHAR.test(source[from - 1])) from -= 1;
  while (to < source.length && COMPLETION_CHAR.test(source[to])) to += 1;

  const location = lineAndColumnAt(source, safeCursor);
  return {
    text: source,
    cursor: safeCursor,
    from,
    to,
    prefix: source.slice(from, safeCursor),
    token: source.slice(from, to),
    line: location.line,
    column: location.column,
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
    .filter((item) => !prefix || String(item.label || "").toLowerCase().startsWith(prefixLower))
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
  return {
    text: nextText,
    cursor: from + insertText.length,
  };
};
