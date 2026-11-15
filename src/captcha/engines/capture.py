"""Capture harness for script-driven engines.

PerimeterX (press/bundle), Imperva (reese84, `___utmvc`), Castle
(castle.browser.js), F5 Shape (TSPD) and CyberSiARA all mint their cookie or
token inside their own script: the script writes `document.cookie`, stashes a
value in `localStorage`, and posts a sensor request to an endpoint of its
choosing. Re-implementing that generator means owning an obfuscated payload
format per release; running the script means only watching what it does.

This module is the watching part. :func:`wrap` returns a program that

1. installs a deterministic browser-shaped realm (document / navigator /
   location / storage / XHR / fetch / crypto / performance),
2. runs the engine script inside `try`/`catch` so a mid-run throw still reports
   what was captured before it,
3. returns one JSON blob: cookies, local/session storage, outbound requests and
   the error text, if any.

:func:`parse` turns that JSON into a :class:`Capture`. Nothing here performs
I/O -- replaying a captured request is the solver's job, so engines stay offline
and testable (`tests/engines`).

Runtime note: the returned program ends in a `return` statement, which is what
`captcha.adapters.jssandbox.LocalRuntime` (py-mini-racer) evaluates -- it wraps
the program in a function. The Node sidecar runs scripts as a program and would
reject the top-level `return`, so script-driven engines are pinned to the local
runtime. A script that awaits a network round-trip before writing its cookie
cannot finish in one synchronous evaluation; those deployments are escalated to
the browser lane instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from captcha.domain.errors import SensorError

#: Browser shims the engine scripts read. Deterministic on purpose (fixed
#: crypto bytes, no clock jitter the caller did not ask for) so a replayed run
#: produces the same payload twice.
PRELUDE: str = r"""
var __cap = { cookies: {}, storage: {}, session: {}, requests: [], errors: [] };
var __COOKIE_ATTRS = { path: 1, domain: 1, expires: 1, "max-age": 1, samesite: 1, secure: 1, httponly: 1, version: 1 };
function __capPairs(raw) {
  var out = {};
  if (typeof raw !== "string") return out;
  raw.split(";").forEach(function (part) {
    var i = part.indexOf("=");
    if (i <= 0) return;
    var name = part.slice(0, i).trim();
    var value = part.slice(i + 1).trim();
    if (!name || __COOKIE_ATTRS[name.toLowerCase()]) return;
    out[name] = value;
  });
  return out;
}
function __capStorage(bucket) {
  return {
    getItem: function (k) { var key = String(k); return Object.prototype.hasOwnProperty.call(bucket, key) ? bucket[key] : null; },
    setItem: function (k, v) { bucket[String(k)] = String(v); },
    removeItem: function (k) { delete bucket[String(k)]; },
    clear: function () { Object.keys(bucket).forEach(function (k) { delete bucket[k]; }); },
    key: function (i) { return Object.keys(bucket)[i] || null; },
    get length() { return Object.keys(bucket).length; }
  };
}
var localStorage = __capStorage(__cap.storage);
var sessionStorage = __capStorage(__cap.session);
var location = { href: "", origin: "", protocol: "https:", host: "", hostname: "", pathname: "/", search: "", hash: "" };
var navigator = {
  userAgent: "",
  language: "en-US",
  languages: ["en-US", "en"],
  platform: "Win32",
  webdriver: false,
  hardwareConcurrency: 8,
  deviceMemory: 8,
  plugins: [],
  mimeTypes: [],
  connection: { effectiveType: "4g", rtt: 50, downlink: 10, saveData: false },
  permissions: { query: function () { return __capThenable({ state: "prompt", onchange: null }); } },
  sendBeacon: function (url, body) {
    __cap.requests.push({ url: String(url), method: "POST", headers: {}, body: body == null ? null : String(body) });
    return true;
  }
};
var performance = {
  now: function () { return 0; },
  timeOrigin: 0,
  timing: {},
  getEntries: function () { return []; },
  getEntriesByType: function () { return []; },
  mark: function () {},
  measure: function () {}
};
var __capRandom = 7;
var crypto = {
  getRandomValues: function (arr) {
    for (var i = 0; i < arr.length; i++) { __capRandom = (__capRandom * 1103515245 + 12345) & 0x7fffffff; arr[i] = __capRandom & 0xff; }
    return arr;
  },
  randomUUID: function () { return "00000000-0000-4000-8000-000000000000"; }
};
var document = {
  get cookie() { return Object.keys(__cap.cookies).map(function (k) { return k + "=" + __cap.cookies[k]; }).join("; "); },
  set cookie(v) { var pairs = __capPairs(v); Object.keys(pairs).forEach(function (k) { __cap.cookies[k] = pairs[k]; }); },
  referrer: "",
  readyState: "complete",
  visibilityState: "visible",
  hidden: false,
  hasFocus: function () { return true; },
  addEventListener: function () {},
  removeEventListener: function () {},
  dispatchEvent: function () { return true; },
  createElement: function () { return { style: {}, dataset: {}, setAttribute: function () {}, appendChild: function () {}, removeChild: function () {}, addEventListener: function () {}, click: function () {} }; },
  createTextNode: function () { return {}; },
  getElementById: function () { return null; },
  getElementsByTagName: function () { return []; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  write: function () {},
  documentElement: {},
  head: {},
  body: {},
  location: location
};
function XMLHttpRequest() {
  var self = this;
  this.readyState = 0;
  this.status = 0;
  this.responseText = "";
  this.response = "";
  this.withCredentials = false;
  this.headers = {};
  this.method = "GET";
  this.url = "";
  this.onreadystatechange = null;
  this.onload = null;
  this.onloadend = null;
  this.onerror = null;
  this.ontimeout = null;
  this.open = function (method, url) { this.method = String(method || "GET").toUpperCase(); this.url = String(url || ""); this.readyState = 1; };
  this.setRequestHeader = function (k, v) { this.headers[String(k)] = String(v); };
  this.getResponseHeader = function () { return null; };
  this.getAllResponseHeaders = function () { return ""; };
  this.abort = function () {};
  this.addEventListener = function () {};
  this.send = function (body) {
    __cap.requests.push({ url: this.url, method: this.method, headers: this.headers, body: body == null ? null : String(body) });
    this.readyState = 4;
    this.status = 204;
    try {
      if (typeof this.onreadystatechange === "function") this.onreadystatechange();
      if (typeof this.onload === "function") this.onload();
      if (typeof this.onloadend === "function") this.onloadend();
    } catch (e) { __cap.errors.push("xhr-callback: " + String(e)); }
    return true;
  };
}
function Worker(url) {
  this.url = String(url || "");
  this.postMessage = function () {};
  this.addEventListener = function () {};
  this.removeEventListener = function () {};
  this.terminate = function () {};
}
var SharedWorker = Worker;
function MessageChannel() {
  this.port1 = { postMessage: function () {}, addEventListener: function () {}, close: function () {} };
  this.port2 = { postMessage: function () {}, addEventListener: function () {}, close: function () {} };
}
var IntersectionObserver = function () { return { observe: function () {}, disconnect: function () {} }; };
var ResizeObserver = function () { return { observe: function () {}, disconnect: function () {} }; };
function __capThenable(value) {
  return {
    then: function (fn) {
      try { return __capThenable(typeof fn === "function" ? fn(value) : value); }
      catch (e) { __cap.errors.push("then: " + String(e)); return __capThenable(undefined); }
    },
    catch: function () { return this; },
    finally: function (fn) { if (typeof fn === "function") fn(); return this; }
  };
}
function fetch(input, init) {
  var options = init || {};
  __cap.requests.push({
    url: String(input && input.url ? input.url : input),
    method: String(options.method || "GET").toUpperCase(),
    headers: options.headers || {},
    body: options.body == null ? null : String(options.body)
  });
  return __capThenable({
    ok: true,
    status: 204,
    statusText: "No Content",
    headers: { get: function () { return null; }, has: function () { return false; } },
    text: function () { return __capThenable(""); },
    json: function () { return __capThenable({}); }
  });
}
var window = {
  document: document,
  navigator: navigator,
  location: location,
  localStorage: localStorage,
  sessionStorage: sessionStorage,
  performance: performance,
  crypto: crypto,
  fetch: fetch,
  XMLHttpRequest: XMLHttpRequest,
  innerWidth: 1920,
  innerHeight: 1080,
  outerWidth: 1920,
  outerHeight: 1080,
  devicePixelRatio: 1,
  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1080, colorDepth: 24, pixelDepth: 24 },
  addEventListener: function () {},
  removeEventListener: function () {},
  setTimeout: function (fn) { if (typeof fn === "function") { try { fn(); } catch (e) { __cap.errors.push("timeout: " + String(e)); } } return 0; },
  clearTimeout: function () {},
  setInterval: function () { return 0; },
  clearInterval: function () {},
  requestAnimationFrame: function (fn) { if (typeof fn === "function") { try { fn(0); } catch (e) { __cap.errors.push("raf: " + String(e)); } } return 0; },
  Worker: Worker,
  SharedWorker: SharedWorker,
  MessageChannel: MessageChannel,
  IntersectionObserver: IntersectionObserver,
  ResizeObserver: ResizeObserver,
  atob: function (s) { return typeof atob === "function" ? atob(s) : ""; },
  btoa: function (s) { return typeof btoa === "function" ? btoa(s) : ""; }
};
window.window = window;
var self = window;
var top = window;
var parent = window;
"""

#: Footer that hands the capture back to Python. Kept next to the prelude
#: because the two must agree on the field names.
FOOTER: str = (
    "return JSON.stringify({cookies: __cap.cookies, storage: __cap.storage, "
    "session: __cap.session, requests: __cap.requests, errors: __cap.errors});"
)

#: Stack snippets longer than this are truncated before they reach an error
#: context: a challenge stack can carry the script URL and megabytes of source.
MAX_ERROR_CHARS = 600


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    """One outbound request the engine script attempted."""

    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None

    @property
    def is_post(self) -> bool:
        return self.method.upper() in ("POST", "PUT", "PATCH")


@dataclass(frozen=True, slots=True)
class Capture:
    """Everything one script run produced."""

    cookies: dict[str, str] = field(default_factory=dict)
    storage: dict[str, str] = field(default_factory=dict)
    session: dict[str, str] = field(default_factory=dict)
    requests: tuple[CapturedRequest, ...] = ()
    errors: tuple[str, ...] = ()

    def cookie(self, *names: str) -> str:
        """First non-empty value among `names`; empty string when absent."""
        for name in names:
            value = self.cookies.get(name, "")
            if value:
                return value
        return ""

    def cookie_with_prefix(self, *prefixes: str) -> tuple[str, str]:
        """`(name, value)` of the first cookie starting with a prefix.

        F5 Shape names its integrity cookie differently per deployment
        (`TS01abc123`, `TSPD_101`), so the name cannot be a constant.
        """
        for name, value in self.cookies.items():
            if value and any(name.startswith(prefix) for prefix in prefixes):
                return name, value
        return "", ""

    def stored(self, *names: str) -> str:
        """A value from local or session storage."""
        for name in names:
            for bucket in (self.storage, self.session):
                value = bucket.get(name, "")
                if value:
                    return value
        return ""

    def token(self, *names: str) -> str:
        """A token-shaped value: storage first (Castle keeps `__cuid` there), then
        cookies."""
        return self.stored(*names) or self.cookie(*names)

    def request(self, *, needle: str = "", method: str | None = None) -> CapturedRequest | None:
        """First captured request whose URL contains `needle` (if given)."""
        wanted = method.upper() if method else None
        for item in self.requests:
            if needle and needle not in item.url:
                continue
            if wanted and item.method.upper() != wanted:
                continue
            return item
        return None

    def error_tail(self) -> str:
        return " | ".join(self.errors)[:MAX_ERROR_CHARS]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cookies": dict(self.cookies),
            "storage": dict(self.storage),
            "session": dict(self.session),
            "requests": [
                {"url": r.url, "method": r.method, "headers": dict(r.headers), "body": r.body} for r in self.requests
            ],
            "errors": list(self.errors),
        }


def wrap(script: str, *, setup: str = "", engine: str = "engine") -> str:
    """Build the capture program for one engine script.

    `setup` runs after the shims and before the script: that is where a caller
    seeds `document.cookie`, storage, `location.href` and the UA from the task, so
    the script sees the same session the client asked for.
    """
    if not isinstance(script, str) or not script.strip():
        raise SensorError(
            f"{engine}: capture harness needs a non-empty script",
            context={"engine": engine},
        )
    return "\n".join(
        (
            PRELUDE,
            setup.strip(),
            "try {",
            script,
            "} catch (e) { __cap.errors.push(String((e && e.stack) || e)); }",
            FOOTER,
        )
    )


def setup_js(
    *,
    href: str = "",
    user_agent: str = "",
    cookies: dict[str, str] | None = None,
    storage: dict[str, str] | None = None,
    referrer: str = "",
) -> str:
    """Seed the realm from a task payload; every value is JSON-encoded."""
    lines: list[str] = []
    if href:
        lines.append(f"location.href = {json.dumps(href)};")
        lines.append(f"location.origin = {json.dumps(_origin(href))};")
        lines.append(f"location.host = {json.dumps(_host(href))};")
        lines.append(f"location.hostname = {json.dumps(_host(href))};")
        lines.append(f"document.referrer = {json.dumps(referrer)};")
    if user_agent:
        lines.append(f"navigator.userAgent = {json.dumps(user_agent)};")
    for name, value in (cookies or {}).items():
        lines.append(f"document.cookie = {json.dumps(f'{name}={value}; path=/')};")
    for name, value in (storage or {}).items():
        lines.append(f"localStorage.setItem({json.dumps(name)}, {json.dumps(value)});")
    return "\n".join(lines)


def parse(raw: Any, *, engine: str) -> Capture:
    """Turn the program's JSON answer into a capture.

    A script that returned nothing or a non-object is a harness/shape problem
    (`SensorError`), not a stale challenge: the solver decides staleness from the
    captured cookies, which is a different failure and a different retry.
    """
    if raw is None:
        raise SensorError(
            f"{engine}: capture script returned nothing",
            context={"engine": engine, "hint": "the runtime must evaluate a function body (LocalRuntime)"},
        )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise SensorError(
                f"{engine}: capture script returned non-JSON",
                context={"engine": engine, "chars": len(raw), "head": raw[:200]},
            ) from exc
    if not isinstance(raw, dict):
        raise SensorError(
            f"{engine}: capture script returned an unexpected type",
            context={"engine": engine, "got": type(raw).__name__},
        )

    requests = [_request(item) for item in _list(raw.get("requests"))]
    return Capture(
        cookies=_string_map(raw.get("cookies")),
        storage=_string_map(raw.get("storage")),
        session=_string_map(raw.get("session")),
        requests=tuple(item for item in requests if item is not None),
        errors=tuple(str(item)[:MAX_ERROR_CHARS] for item in _list(raw.get("errors"))),
    )


def _request(raw: Any) -> CapturedRequest | None:
    if not isinstance(raw, dict) or not raw.get("url"):
        return None
    body = raw.get("body")
    return CapturedRequest(
        url=str(raw["url"]),
        method=str(raw.get("method") or "GET").upper(),
        headers=_string_map(raw.get("headers")),
        body=None if body is None else str(body),
    )


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}


def _list(raw: Any) -> list[Any]:
    return list(raw) if isinstance(raw, list | tuple) else []


def _origin(url: str) -> str:
    parts = url.split("://", 1)
    if len(parts) != 2:
        return "https://" + url.split("/", 1)[0]
    scheme, rest = parts
    return f"{scheme}://{rest.split('/', 1)[0]}"


def _host(url: str) -> str:
    return _origin(url).split("://", 1)[-1].split(":", 1)[0]


__all__ = ["FOOTER", "PRELUDE", "Capture", "CapturedRequest", "parse", "setup_js", "wrap"]
