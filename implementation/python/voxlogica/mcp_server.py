"""Playwright-backed MCP server for inspecting and operating VoxLogicA live."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


_AUTOMATION_BRIDGE_NAME = "__VOXLOGICA_AUTOMATION__"
_START_PROGRAM_STORAGE_KEY = "voxlogica.start.program.v1"

_INSPECT_PAGE_SCRIPT = r"""
({ rootSelector, maxElements, maxDomChars, includeHtml }) => {
  const root = rootSelector ? document.querySelector(rootSelector) : document.documentElement;
  if (!root) {
    return {
      error: `No element matches selector: ${rootSelector}`,
      title: document.title,
      url: window.location.href,
      readyState: document.readyState,
    };
  }

  const cssEscape = (value) => {
    const text = String(value ?? "");
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(text);
    }
    return text.replace(/([ !"#$%&'()*+,./:;<=>?@[\\\]^`{|}~])/g, "\\$1");
  };

  const normalizeText = (value, maxLength = 120) => {
    return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, maxLength);
  };

  const isVisible = (element) => {
    if (!(element instanceof Element)) {
      return false;
    }
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const buildSelector = (element) => {
    if (!(element instanceof Element)) {
      return "";
    }
    if (element.id) {
      return `#${cssEscape(element.id)}`;
    }
    const dataTestId = element.getAttribute("data-testid");
    if (dataTestId) {
      return `${element.tagName.toLowerCase()}[data-testid="${String(dataTestId).replace(/"/g, '\\"')}"]`;
    }
    const parts = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += `#${cssEscape(node.id)}`;
        parts.unshift(part);
        break;
      }
      const nodeTestId = node.getAttribute("data-testid");
      if (nodeTestId) {
        part += `[data-testid="${String(nodeTestId).replace(/"/g, '\\"')}"]`;
        parts.unshift(part);
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((child) => child.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = parent;
      if (!node || node === document.documentElement) {
        break;
      }
    }
    return parts.join(" > ");
  };

  const actionList = (element) => {
    const actions = new Set();
    const tag = element.tagName.toLowerCase();
    const role = String(element.getAttribute("role") || "").toLowerCase();
    const inputType = String(element.getAttribute("type") || "").toLowerCase();
    const disabled = Boolean(element.disabled) || element.getAttribute("aria-disabled") === "true";
    if (disabled) {
      return [];
    }

    if (tag === "a" || tag === "button" || tag === "summary" || role === "button" || role === "link" || element.hasAttribute("onclick")) {
      actions.add("click");
    }
    if (tag === "input") {
      if (["", "text", "email", "search", "url", "tel", "password", "number", "date", "time"].includes(inputType)) {
        actions.add("type");
      }
      if (["button", "submit", "checkbox", "radio", "range", "color", "file"].includes(inputType)) {
        actions.add("click");
      }
    }
    if (tag === "textarea" || element.isContentEditable) {
      actions.add("type");
    }
    if (tag === "select") {
      actions.add("select");
    }
    if (element.tabIndex >= 0 && actions.size === 0) {
      actions.add("focus");
    }
    return Array.from(actions);
  };

  const candidates = Array.from(
    root.querySelectorAll(
      [
        "a[href]",
        "button",
        "input:not([type='hidden'])",
        "select",
        "textarea",
        "summary",
        "[role='button']",
        "[role='link']",
        "[onclick]",
        "[tabindex]",
        "[contenteditable='true']",
      ].join(", "),
    ),
  );

  const interactive = [];
  const seen = new Set();
  for (const element of candidates) {
    if (seen.has(element) || !isVisible(element)) {
      continue;
    }
    const actions = actionList(element);
    if (actions.length === 0) {
      continue;
    }
    seen.add(element);
    const rect = element.getBoundingClientRect();
    interactive.push({
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute("role") || null,
      selector: buildSelector(element),
      actions,
      text: normalizeText(element.innerText || element.textContent || element.value || ""),
      aria_label: element.getAttribute("aria-label") || null,
      title: element.getAttribute("title") || null,
      href: element.getAttribute("href") || null,
      input_type: element.getAttribute("type") || null,
      disabled: Boolean(element.disabled) || element.getAttribute("aria-disabled") === "true",
      bounding_box: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
    });
    if (interactive.length >= maxElements) {
      break;
    }
  }

  const active = document.activeElement instanceof Element
    ? {
        tag: document.activeElement.tagName.toLowerCase(),
        selector: buildSelector(document.activeElement),
        text: normalizeText(document.activeElement.innerText || document.activeElement.textContent || document.activeElement.value || ""),
      }
    : null;

  return {
    title: document.title,
    url: window.location.href,
    readyState: document.readyState,
    root_selector: rootSelector || "document.documentElement",
    dom_excerpt: includeHtml ? root.outerHTML.slice(0, maxDomChars) : null,
    text_excerpt: normalizeText(root.innerText || root.textContent || "", Math.min(maxDomChars, 2000)),
    interactive_elements: interactive,
    interactive_count: interactive.length,
    action_areas: {
      browser: ["open_page", "inspect_page", "focus_app", "close_browser"],
      ui: ["inspect_app_state", "select_app_tab", "click_element", "focus_element", "read_element_text"],
      program: ["read_program", "set_program", "click_variable"],
      runtime: [
        "inspect_runtime_state",
        "list_playground_jobs",
        "get_playground_job",
        "kill_playground_job",
        "get_program_symbols",
        "get_program_graph",
        "resolve_program_value",
        "resolve_program_value_page",
      ],
    },
    active_element: active,
  };
}
"""

_CALL_BRIDGE_SCRIPT = r"""
async ({ bridgeName, methodName, args }) => {
  const bridge = globalThis?.[bridgeName];
  if (!bridge) {
    return { ok: false, error: `Automation bridge not found: ${bridgeName}` };
  }
  const method = bridge?.[methodName];
  if (typeof method !== "function") {
    return { ok: false, error: `Automation bridge method not found: ${methodName}` };
  }
  try {
    const result = await method(...(Array.isArray(args) ? args : []));
    return { ok: true, result: result ?? null };
  } catch (error) {
    return {
      ok: false,
      error: String(error?.stack || error?.message || error),
    };
  }
}
"""

_FETCH_JSON_SCRIPT = r"""
async ({ path, method, body }) => {
  const init = {
    method: method || "GET",
    headers: {
      "Accept": "application/json",
    },
  };
  if (body !== null && body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  const rawText = await response.text();
  let payload = null;
  if (rawText) {
    try {
      payload = JSON.parse(rawText);
    } catch {
      payload = rawText;
    }
  }
  return {
    ok: response.ok,
    status: response.status,
    statusText: response.statusText,
    payload,
  };
}
"""


class UIInspectorSession:
    """Manage a single reusable Playwright browser session for MCP tools."""

    def __init__(
        self,
        *,
        start_url: str | None = None,
        headless: bool = True,
        browser_channel: str | None = None,
        viewport_width: int = 1440,
        viewport_height: int = 900,
    ) -> None:
        self._start_url = str(start_url).strip() if start_url else None
        self._headless = bool(headless)
        self._browser_channel = str(browser_channel).strip() if browser_channel else None
        self._viewport_width = int(viewport_width)
        self._viewport_height = int(viewport_height)
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def _ensure_browser(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": self._headless}
        if self._browser_channel:
            launch_kwargs["channel"] = self._browser_channel
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            viewport={"width": self._viewport_width, "height": self._viewport_height},
            ignore_https_errors=True,
        )
        return self._context

    async def _get_page(self, *, auto_open: bool) -> Page:
        context = await self._ensure_browser()
        if self._page is None or self._page.is_closed():
            self._page = await context.new_page()
        if auto_open and self._start_url and self._page.url in {"", "about:blank"}:
            await self._page.goto(self._start_url, wait_until="domcontentloaded")
        return self._page

    async def _require_page(self) -> Page:
        page = await self._get_page(auto_open=True)
        if page.url in {"", "about:blank"}:
            raise ValueError("No page is open. Call open_page first.")
        return page

    async def _call_bridge(self, method_name: str, *args: Any) -> Any:
        page = await self._require_page()
        payload = await page.evaluate(
            _CALL_BRIDGE_SCRIPT,
            {
                "bridgeName": _AUTOMATION_BRIDGE_NAME,
                "methodName": str(method_name or ""),
                "args": list(args),
            },
        )
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = payload.get("error") if isinstance(payload, dict) else "Bridge invocation failed."
            raise RuntimeError(str(error))
        return payload.get("result")

    async def _fetch_backend_json(self, path: str, *, method: str = "GET", body: Any = None) -> dict[str, Any]:
        page = await self._require_page()
        payload = await page.evaluate(
            _FETCH_JSON_SCRIPT,
            {
                "path": str(path or ""),
                "method": str(method or "GET").upper(),
                "body": body,
            },
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"Backend fetch returned an invalid response for {path}.")
        if not payload.get("ok"):
            detail = payload.get("payload")
            raise RuntimeError(
                f"Backend fetch failed for {path}: status={payload.get('status')} detail={detail!r}"
            )
        return payload

    async def _read_program_text(self) -> str:
        try:
            program = await self._call_bridge("getProgram")
            return str(program or "")
        except Exception:
            page = await self._require_page()
            program = await page.evaluate(
                """
                ({ storageKey }) => {
                  try {
                    return globalThis?.localStorage?.getItem(storageKey) || "";
                  } catch {
                    return "";
                  }
                }
                """,
                {"storageKey": _START_PROGRAM_STORAGE_KEY},
            )
            return str(program or "")

    async def open_page(
        self,
        *,
        url: str | None = None,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 15000,
    ) -> dict[str, Any]:
        target_url = str(url or self._start_url or "").strip()
        if not target_url:
            raise ValueError("No target URL configured. Pass --url when starting the server or call open_page with a URL.")
        async with self._lock:
            page = await self._get_page(auto_open=False)
            try:
                await page.goto(target_url, wait_until=wait_until, timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                raise RuntimeError(f"Timed out while opening {target_url}.") from exc
            return {
                "url": page.url,
                "title": await page.title(),
                "ready_state": await page.evaluate("() => document.readyState"),
            }

    async def inspect_page(
        self,
        *,
        root_selector: str | None = None,
        max_elements: int = 200,
        max_dom_chars: int = 12000,
        include_html: bool = True,
    ) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            payload = await page.evaluate(
                _INSPECT_PAGE_SCRIPT,
                {
                    "rootSelector": root_selector,
                    "maxElements": int(max_elements),
                    "maxDomChars": int(max_dom_chars),
                    "includeHtml": bool(include_html),
                },
            )
            if isinstance(payload, dict) and payload.get("error"):
                raise ValueError(str(payload["error"]))
            return payload

    async def focus_app(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            await page.bring_to_front()
            details = await page.evaluate(
                """
                () => {
                  try {
                    window.focus();
                  } catch {}
                  try {
                    document.body?.focus?.();
                  } catch {}
                  const active = document.activeElement instanceof Element
                    ? {
                        tag: document.activeElement.tagName.toLowerCase(),
                        aria_label: document.activeElement.getAttribute("aria-label") || null,
                      }
                    : null;
                  return {
                    ready_state: document.readyState,
                    active_element: active,
                  };
                }
                """
            )
            return {
                "url": page.url,
                "title": await page.title(),
                "focused": True,
                **(details if isinstance(details, dict) else {}),
            }

    async def click_element(
        self,
        *,
        selector: str,
        wait_for: str | None = None,
        timeout_ms: int = 10000,
    ) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            locator = page.locator(selector).first
            await locator.click(timeout=timeout_ms)
            if wait_for:
                await page.locator(wait_for).first.wait_for(timeout=timeout_ms)
            return {
                "url": page.url,
                "title": await page.title(),
                "clicked": selector,
                "ready_state": await page.evaluate("() => document.readyState"),
            }

    async def type_text(
        self,
        *,
        selector: str,
        text: str,
        clear_first: bool = True,
        submit: bool = False,
        timeout_ms: int = 10000,
    ) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            locator = page.locator(selector).first
            await locator.wait_for(timeout=timeout_ms)
            if clear_first:
                await locator.fill("")
            await locator.type(text, timeout=timeout_ms)
            if submit:
                await locator.press("Enter", timeout=timeout_ms)
            value = await locator.input_value(timeout=timeout_ms)
            return {
                "url": page.url,
                "title": await page.title(),
                "typed_into": selector,
                "value": value,
            }

    async def select_option(
        self,
        *,
        selector: str,
        value: str,
        timeout_ms: int = 10000,
    ) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            locator = page.locator(selector).first
            await locator.select_option(value=value, timeout=timeout_ms)
            selected = await locator.input_value(timeout=timeout_ms)
            return {
                "url": page.url,
                "title": await page.title(),
                "selector": selector,
                "selected_value": selected,
            }

    async def focus_element(self, *, selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            locator = page.locator(selector).first
            await locator.focus(timeout=timeout_ms)
            active_selector = await page.evaluate(
                """
                (selector) => {
                  const element = document.activeElement;
                  return {
                    focused: selector,
                    tag: element ? element.tagName.toLowerCase() : null,
                    text: element ? String(element.innerText || element.textContent || element.value || '').trim().slice(0, 120) : null,
                  };
                }
                """,
                selector,
            )
            return {
                "url": page.url,
                "title": await page.title(),
                **active_selector,
            }

    async def read_element_text(self, *, selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        async with self._lock:
            page = await self._require_page()
            locator = page.locator(selector).first
            await locator.wait_for(timeout=timeout_ms)
            payload = await locator.evaluate(
                """
                (element) => {
                  const rawValue = typeof element.value === "string" ? element.value : null;
                  const innerText = typeof element.innerText === "string" ? element.innerText : "";
                  const textContent = typeof element.textContent === "string" ? element.textContent : "";
                  const text = rawValue ?? (innerText || textContent);
                  return {
                    tag: element.tagName.toLowerCase(),
                    role: element.getAttribute("role") || null,
                    aria_label: element.getAttribute("aria-label") || null,
                    contenteditable: element.getAttribute("contenteditable") || null,
                    text,
                    value: rawValue,
                  };
                }
                """,
            )
            return {
                "url": page.url,
                "title": await page.title(),
                "selector": selector,
                **payload,
            }

    async def inspect_app_state(self) -> dict[str, Any]:
        async with self._lock:
            state = await self._call_bridge("getAppState")
            return state if isinstance(state, dict) else {"state": state}

    async def select_app_tab(self, tab_id: str) -> dict[str, Any]:
        async with self._lock:
            result = await self._call_bridge("selectTab", str(tab_id or ""))
            return result if isinstance(result, dict) else {"result": result}

    async def read_program(self) -> dict[str, Any]:
        async with self._lock:
            program = await self._read_program_text()
            return {
                "text": program,
                "length": len(program),
                "storage_key": _START_PROGRAM_STORAGE_KEY,
            }

    async def set_program(self, program_text: str, *, run_after_load: bool = False) -> dict[str, Any]:
        async with self._lock:
            result = await self._call_bridge("loadProgram", str(program_text or ""), bool(run_after_load))
            return result if isinstance(result, dict) else {"result": result}

    async def click_variable(self, token: str) -> dict[str, Any]:
        async with self._lock:
            result = await self._call_bridge("selectStartSymbol", str(token or ""))
            return result if isinstance(result, dict) else {"result": result}

    async def list_playground_jobs(self) -> dict[str, Any]:
        async with self._lock:
            return await self._fetch_backend_json("/api/v1/playground/jobs")

    async def get_playground_job(self, job_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self._fetch_backend_json(f"/api/v1/playground/jobs/{job_id}")

    async def kill_playground_job(self, job_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self._fetch_backend_json(f"/api/v1/playground/jobs/{job_id}", method="DELETE")

    async def get_program_symbols(self, *, program_text: str | None = None) -> dict[str, Any]:
        async with self._lock:
            program = str(program_text) if program_text is not None else await self._read_program_text()
            return await self._fetch_backend_json(
                "/api/v1/playground/symbols",
                method="POST",
                body={"program": program},
            )

    async def get_program_graph(self, *, program_text: str | None = None) -> dict[str, Any]:
        async with self._lock:
            program = str(program_text) if program_text is not None else await self._read_program_text()
            return await self._fetch_backend_json(
                "/api/v1/playground/graph",
                method="POST",
                body={"program": program},
            )

    async def resolve_program_value(
        self,
        *,
        variable: str = "",
        node_id: str = "",
        path: str = "",
        enqueue: bool = False,
        program_text: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            program = str(program_text) if program_text is not None else await self._read_program_text()
            payload: dict[str, Any] = {
                "program": program,
                "execution_strategy": "dask",
                "variable": str(variable or ""),
                "path": str(path or ""),
                "enqueue": bool(enqueue),
            }
            if not payload["variable"] and node_id:
                payload["node_id"] = str(node_id)
            return await self._fetch_backend_json(
                "/api/v1/playground/value",
                method="POST",
                body=payload,
            )

    async def resolve_program_value_page(
        self,
        *,
        variable: str = "",
        node_id: str = "",
        path: str = "",
        offset: int = 0,
        limit: int = 64,
        enqueue: bool = False,
        program_text: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            program = str(program_text) if program_text is not None else await self._read_program_text()
            payload: dict[str, Any] = {
                "program": program,
                "execution_strategy": "dask",
                "variable": str(variable or ""),
                "path": str(path or ""),
                "offset": max(0, int(offset)),
                "limit": max(1, int(limit)),
                "enqueue": bool(enqueue),
            }
            if not payload["variable"] and node_id:
                payload["node_id"] = str(node_id)
            return await self._fetch_backend_json(
                "/api/v1/playground/value/page",
                method="POST",
                body=payload,
            )

    async def inspect_runtime_state(
        self,
        *,
        include_symbols: bool = True,
        include_graph: bool = False,
        include_jobs: bool = True,
    ) -> dict[str, Any]:
        async with self._lock:
            app_state: dict[str, Any]
            try:
                bridge_state = await self._call_bridge("getAppState")
                app_state = bridge_state if isinstance(bridge_state, dict) else {"state": bridge_state}
            except Exception as exc:
                app_state = {"error": str(exc)}

            program = await self._read_program_text()
            result: dict[str, Any] = {
                "app": app_state,
                "program": {
                    "text": program,
                    "length": len(program),
                    "storage_key": _START_PROGRAM_STORAGE_KEY,
                },
            }

            if include_jobs:
                jobs_payload = await self._fetch_backend_json("/api/v1/playground/jobs")
                result["jobs"] = jobs_payload.get("payload")

            if program.strip():
                if include_symbols:
                    symbols_payload = await self._fetch_backend_json(
                        "/api/v1/playground/symbols",
                        method="POST",
                        body={"program": program},
                    )
                    result["symbols"] = symbols_payload.get("payload")
                if include_graph:
                    graph_payload = await self._fetch_backend_json(
                        "/api/v1/playground/graph",
                        method="POST",
                        body={"program": program},
                    )
                    result["graph"] = graph_payload.get("payload")
            return result

    async def close_browser(self) -> dict[str, Any]:
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                await self._page.close()
            self._page = None
            if self._context is not None:
                await self._context.close()
            self._context = None
            if self._browser is not None:
                await self._browser.close()
            self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
            self._playwright = None
            return {"closed": True}


def _register_browser_tools(server: FastMCP, session: UIInspectorSession) -> None:
    @server.tool(description="Browser area: open a page in the Playwright browser session.")
    async def open_page(
        url: str | None = None,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 15000,
    ) -> dict[str, Any]:
        return await session.open_page(url=url, wait_until=wait_until, timeout_ms=timeout_ms)

    @server.tool(description="Browser area: inspect the current page and return a DOM excerpt plus visible interactive elements.")
    async def inspect_page(
        root_selector: str | None = None,
        max_elements: int = 200,
        max_dom_chars: int = 12000,
        include_html: bool = True,
    ) -> dict[str, Any]:
        return await session.inspect_page(
            root_selector=root_selector,
            max_elements=max_elements,
            max_dom_chars=max_dom_chars,
            include_html=include_html,
        )

    @server.tool(description="Browser area: bring the live app window to the front and focus it.")
    async def focus_app() -> dict[str, Any]:
        return await session.focus_app()

    @server.tool(description="Browser area: close the Playwright browser and discard the current page session.")
    async def close_browser() -> dict[str, Any]:
        return await session.close_browser()


def _register_ui_tools(server: FastMCP, session: UIInspectorSession) -> None:
    @server.tool(description="UI area: inspect the app bridge state including active tab and Start tab state.")
    async def inspect_app_state() -> dict[str, Any]:
        return await session.inspect_app_state()

    @server.tool(description="UI area: switch the running app to a named tab such as start, graph, results, or compute-log.")
    async def select_app_tab(tab_id: str) -> dict[str, Any]:
        return await session.select_app_tab(tab_id)

    @server.tool(description="UI area: click a visible element using a CSS selector returned by inspect_page.")
    async def click_element(selector: str, wait_for: str | None = None, timeout_ms: int = 10000) -> dict[str, Any]:
        return await session.click_element(selector=selector, wait_for=wait_for, timeout_ms=timeout_ms)

    @server.tool(description="UI area: move focus to an element using a CSS selector returned by inspect_page.")
    async def focus_element(selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await session.focus_element(selector=selector, timeout_ms=timeout_ms)

    @server.tool(description="UI area: read the current text content or value from a visible element such as the Start tab code editor.")
    async def read_element_text(selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await session.read_element_text(selector=selector, timeout_ms=timeout_ms)

    @server.tool(description="UI area: type into an input, textarea, or contenteditable element.")
    async def type_text(
        selector: str,
        text: str,
        clear_first: bool = True,
        submit: bool = False,
        timeout_ms: int = 10000,
    ) -> dict[str, Any]:
        return await session.type_text(
            selector=selector,
            text=text,
            clear_first=clear_first,
            submit=submit,
            timeout_ms=timeout_ms,
        )

    @server.tool(description="UI area: select an option in a <select> element by value.")
    async def select_option(selector: str, value: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await session.select_option(selector=selector, value=value, timeout_ms=timeout_ms)


def _register_program_tools(server: FastMCP, session: UIInspectorSession) -> None:
    @server.tool(description="Program area: read the current Start editor program from the live app.")
    async def read_program() -> dict[str, Any]:
        return await session.read_program()

    @server.tool(description="Program area: replace the Start editor program in the live app and optionally run it immediately.")
    async def set_program(program_text: str, run_after_load: bool = False) -> dict[str, Any]:
        return await session.set_program(program_text, run_after_load=run_after_load)

    @server.tool(description="Program area: select or click a variable token in the Start tab and resolve it live.")
    async def click_variable(token: str) -> dict[str, Any]:
        return await session.click_variable(token)


def _register_runtime_tools(server: FastMCP, session: UIInspectorSession) -> None:
    @server.tool(description="Runtime area: inspect live app state, current program, backend jobs, and optionally symbols or graph in one combined payload.")
    async def inspect_runtime_state(
        include_symbols: bool = True,
        include_graph: bool = False,
        include_jobs: bool = True,
    ) -> dict[str, Any]:
        return await session.inspect_runtime_state(
            include_symbols=include_symbols,
            include_graph=include_graph,
            include_jobs=include_jobs,
        )

    @server.tool(description="Runtime area: list live playground jobs from the running backend queue.")
    async def list_playground_jobs() -> dict[str, Any]:
        return await session.list_playground_jobs()

    @server.tool(description="Runtime area: inspect one live playground job by id.")
    async def get_playground_job(job_id: str) -> dict[str, Any]:
        return await session.get_playground_job(job_id)

    @server.tool(description="Runtime area: kill one live playground job by id.")
    async def kill_playground_job(job_id: str) -> dict[str, Any]:
        return await session.kill_playground_job(job_id)

    @server.tool(description="Runtime area: parse the current program or an explicit program text and return the live symbol table.")
    async def get_program_symbols(program_text: str | None = None) -> dict[str, Any]:
        return await session.get_program_symbols(program_text=program_text)

    @server.tool(description="Runtime area: return the compute graph for the current program or an explicit program text.")
    async def get_program_graph(program_text: str | None = None) -> dict[str, Any]:
        return await session.get_program_graph(program_text=program_text)

    @server.tool(description="Runtime area: resolve one value through the live backend using the current editor program unless an explicit program is supplied.")
    async def resolve_program_value(
        variable: str = "",
        node_id: str = "",
        path: str = "",
        enqueue: bool = False,
        program_text: str | None = None,
    ) -> dict[str, Any]:
        return await session.resolve_program_value(
            variable=variable,
            node_id=node_id,
            path=path,
            enqueue=enqueue,
            program_text=program_text,
        )

    @server.tool(description="Runtime area: resolve one value page through the live backend using the current editor program unless an explicit program is supplied.")
    async def resolve_program_value_page(
        variable: str = "",
        node_id: str = "",
        path: str = "",
        offset: int = 0,
        limit: int = 64,
        enqueue: bool = False,
        program_text: str | None = None,
    ) -> dict[str, Any]:
        return await session.resolve_program_value_page(
            variable=variable,
            node_id=node_id,
            path=path,
            offset=offset,
            limit=limit,
            enqueue=enqueue,
            program_text=program_text,
        )


def build_ui_inspector_mcp_server(
    *,
    start_url: str | None = None,
    headless: bool = True,
    browser_channel: str | None = None,
    viewport_width: int = 1440,
    viewport_height: int = 900,
) -> FastMCP:
    """Create an MCP server that can inspect and operate a running VoxLogicA UI."""

    session = UIInspectorSession(
        start_url=start_url,
        headless=headless,
        browser_channel=browser_channel,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    server = FastMCP("voxlogica-ui-inspector")
    _register_browser_tools(server, session)
    _register_ui_tools(server, session)
    _register_program_tools(server, session)
    _register_runtime_tools(server, session)
    return server


def run_ui_inspector_mcp_server(
    *,
    start_url: str | None = None,
    headless: bool = True,
    browser_channel: str | None = None,
    viewport_width: int = 1440,
    viewport_height: int = 900,
) -> None:
    """Run the UI inspector MCP server over stdio."""

    server = build_ui_inspector_mcp_server(
        start_url=start_url,
        headless=headless,
        browser_channel=browser_channel,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    server.run(transport="stdio")