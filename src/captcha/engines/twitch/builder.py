"""Twitch Integrity sensor builder.

Twitch mints the Client-Integrity JWT from the Kasada KPSDK bootstrap
(p.js). The bootstrap patches window.fetch and attaches x-kpsdk-* headers
to the request the player makes to /integrity; there is no server-side
bundle we could call instead.

So the builder prepares two things: the request envelope (headers the
frontend sends) and a driver script that loads p.js inside the shared JS
runtime, fires the KPSDK lifecycle events, and captures the headers the
patched fetch produced. The solver awaits build(), runs the driver, then
performs the real POST itself -- builders never touch the network.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder

#: Twitch gates GQL behind an integrity token minted here. The request is an
#: empty POST; the answer is {"token": "v4.public...", "expiration": <ms>}.
INTEGRITY_URL: str = "https://gql.twitch.tv/integrity"

#: Client-ID of the Twitch web player. Rotates rarely; override via payload.
WEB_CLIENT_ID: str = "kimne78kx3ncx6brgo4mv6wki5h1ko"

#: The public player page; the browser fallback loads it before fetching.
DEFAULT_PAGE_URL: str = "https://www.twitch.tv/"

#: Kasada bootstrap served for gql.twitch.tv/integrity (public, streamlink PR 5414).
KPSDK_SCRIPT_URL: str = (
    "https://k.twitchcdn.net/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/p.js"
)

#: KPSDK.configure() target the player registers for the integrity call.
KPSDK_TARGET: dict[str, str] = {
    "protocol": "https:",
    "method": "POST",
    "domain": "gql.twitch.tv",
    "path": "/integrity",
}

#: Token envelope: base64url(payload_json || 64-byte signature).
INTEGRITY_PREFIX: str = "v4.public."
SIGNATURE_BYTES: int = 64

#: Browser-surface shim p.js expects. Deterministic on purpose: the same
#: fingerprint must hash to the same KPSDK headers on every retry.
_SHIM = r'''
var __tw = { requests: [], errors: [], timers: [], listeners: {} };

function __twEl(tag) {
  return {
    tagName: tag ? String(tag).toUpperCase() : "DIV",
    style: {}, dataset: {}, attributes: {},
    setAttribute: function (name, value) { this.attributes[name] = String(value); },
    getAttribute: function (name) { return this.attributes[name] === undefined ? null : this.attributes[name]; },
    removeAttribute: function (name) { delete this.attributes[name]; },
    appendChild: function (child) { return child; },
    removeChild: function (child) { return child; },
    insertBefore: function (node) { return node; },
    addEventListener: function () {},
    removeEventListener: function () {},
    dispatchEvent: function () { return true; },
    remove: function () {},
    click: function () {},
    focus: function () {},
    blur: function () {},
    getContext: function () { return null; },
    toDataURL: function () { return "data:,"; },
    textContent: "", innerHTML: "", id: "", src: "", href: "", rel: "", type: "", nonce: ""
  };
}

function __twRecord(url, options) {
  var headers = {};
  var source = options ? options.headers : null;
  if (source) {
    if (Array.isArray(source)) {
      for (var i = 0; i < source.length; i++) { headers[String(source[i][0]).toLowerCase()] = String(source[i][1]); }
    } else if (typeof source.forEach === "function") {
      source.forEach(function (value, name) { headers[String(name).toLowerCase()] = String(value); });
    } else {
      for (var name in source) {
        if (Object.prototype.hasOwnProperty.call(source, name)) { headers[String(name).toLowerCase()] = String(source[name]); }
      }
    }
  }
  __tw.requests.push({
    url: String(url),
    method: (options && options.method) || "GET",
    headers: headers,
    body: options ? options.body : null
  });
}

function __twResponse() {
  return {
    ok: true, status: 200, statusText: "OK", url: TW_URL, redirected: false, type: "cors",
    headers: { get: function () { return null; }, forEach: function () {} },
    json: function () { return Promise.resolve({}); },
    text: function () { return Promise.resolve(""); },
    arrayBuffer: function () { return Promise.resolve(new ArrayBuffer(0)); },
    clone: function () { return __twResponse(); }
  };
}

function __twDrain() {
  var budget = 512;
  while (__tw.timers.length && budget-- > 0) {
    var item = __tw.timers.shift();
    try { item[0].call(globalThis, TW_TS); } catch (error) { __tw.errors.push(String(error)); }
  }
}

if (typeof globalThis.window === "undefined") { globalThis.window = globalThis; }
if (typeof globalThis.self === "undefined") { globalThis.self = globalThis; }
if (typeof globalThis.top === "undefined") { globalThis.top = globalThis; }
if (typeof globalThis.parent === "undefined") { globalThis.parent = globalThis; }

globalThis.navigator = {
  userAgent: TW_UA, appVersion: TW_UA, platform: "Win32", vendor: "Google Inc.",
  language: "en-US", languages: ["en-US", "en"], webdriver: false,
  hardwareConcurrency: 8, deviceMemory: 8, plugins: [], mimeTypes: [], maxTouchPoints: 0,
  cookieEnabled: true, doNotTrack: null, onLine: true
};
globalThis.screen = {
  width: 1920, height: 1080, availWidth: 1920, availHeight: 1040,
  colorDepth: 24, pixelDepth: 24, orientation: { type: "landscape-primary", angle: 0 }
};
globalThis.location = {
  href: TW_SITE, protocol: "https:", host: TW_HOST, hostname: TW_HOST,
  origin: "https://" + TW_HOST, pathname: "/", search: "", hash: "",
  reload: function () {}, assign: function () {}, replace: function () {}
};
globalThis.history = { length: 2, state: null, pushState: function () {}, replaceState: function () {}, back: function () {} };
globalThis.performance = { now: function () { return TW_TS; }, timeOrigin: TW_TS, getEntriesByType: function () { return []; } };
globalThis.crypto = globalThis.crypto || {
  getRandomValues: function (array) {
    for (var i = 0; i < array.length; i++) { array[i] = (i * 37 + 11) & 255; }
    return array;
  },
  randomUUID: function () { return "00000000-0000-4000-8000-000000000000"; }
};
globalThis.localStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {}, clear: function () {} };
globalThis.sessionStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {}, clear: function () {} };

globalThis.document = {
  readyState: "complete", cookie: TW_COOKIES, referrer: "", title: "Twitch",
  visibilityState: "visible", hidden: false,
  hasFocus: function () { return true; },
  createElement: function (tag) { return __twEl(tag); },
  createTextNode: function (text) { return { textContent: String(text) }; },
  getElementById: function () { return null; },
  getElementsByTagName: function () { return []; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function (name, fn) { (__tw.listeners[name] || (__tw.listeners[name] = [])).push(fn); },
  removeEventListener: function () {},
  dispatchEvent: function (event) {
    var name = event ? event.type : null;
    var handlers = (__tw.listeners[name] || []).slice();
    for (var i = 0; i < handlers.length; i++) {
      try { handlers[i].call(globalThis, event); } catch (error) { __tw.errors.push(String(error)); }
    }
    return true;
  },
  head: __twEl("head"), body: __twEl("body"), documentElement: __twEl("html"),
  fonts: { ready: Promise.resolve(), add: function () {}, check: function () { return true; } }
};

globalThis.setTimeout = function (fn, delay) { __tw.timers.push([fn, delay || 0]); return __tw.timers.length; };
globalThis.setInterval = function () { return 0; };
globalThis.clearTimeout = function () {};
globalThis.clearInterval = function () {};
globalThis.requestAnimationFrame = function (fn) { __tw.timers.push([fn, 0]); return __tw.timers.length; };
globalThis.cancelAnimationFrame = function () {};
globalThis.addEventListener = function (name, fn) { (__tw.listeners[name] || (__tw.listeners[name] = [])).push(fn); };
globalThis.removeEventListener = function () {};
globalThis.dispatchEvent = function (event) {
  var name = event ? event.type : null;
  var handlers = (__tw.listeners[name] || []).slice();
  for (var i = 0; i < handlers.length; i++) { handlers[i].call(globalThis, event); }
  return true;
};

var __TW_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
globalThis.btoa = function (input) {
  var text = String(input), out = "", index = 0;
  while (index < text.length) {
    var a = text.charCodeAt(index++) & 255;
    var b = index < text.length ? text.charCodeAt(index++) & 255 : NaN;
    var c = index < text.length ? text.charCodeAt(index++) & 255 : NaN;
    out += __TW_ALPHABET.charAt(a >> 2);
    out += __TW_ALPHABET.charAt(((a & 3) << 4) | (isNaN(b) ? 0 : b >> 4));
    out += isNaN(b) ? "=" : __TW_ALPHABET.charAt(((b & 15) << 2) | (isNaN(c) ? 0 : c >> 6));
    out += isNaN(c) ? "=" : __TW_ALPHABET.charAt(c & 63);
  }
  return out;
};
globalThis.atob = function (input) {
  var text = String(input).replace(/[^A-Za-z0-9+/=]/g, ""), out = "", index = 0;
  while (index < text.length) {
    var a = __TW_ALPHABET.indexOf(text.charAt(index++));
    var b = __TW_ALPHABET.indexOf(text.charAt(index++));
    var c = __TW_ALPHABET.indexOf(text.charAt(index++));
    var d = __TW_ALPHABET.indexOf(text.charAt(index++));
    out += String.fromCharCode((a << 2) | (b >> 4));
    if (c !== -1) { out += String.fromCharCode(((b & 15) << 4) | (c >> 2)); }
    if (d !== -1) { out += String.fromCharCode(((c & 3) << 6) | d); }
  }
  return out;
};

globalThis.fetch = function (url, options) { __twRecord(url, options); return Promise.resolve(__twResponse()); };
globalThis.XMLHttpRequest = function () {
  var request = this;
  this.readyState = 0; this.status = 0; this.responseText = "{}"; this.response = "{}";
  this.open = function (method, url) { request._method = method; request._url = url; request.readyState = 1; };
  this.setRequestHeader = function (name, value) { (request._headers || (request._headers = {}))[name] = value; };
  this.send = function (body) {
    __twRecord(request._url, { method: request._method, headers: request._headers, body: body });
    request.readyState = 4; request.status = 200;
    if (typeof request.onreadystatechange === "function") {
      try { request.onreadystatechange(); } catch (error) { __tw.errors.push(String(error)); }
    }
    if (typeof request.onload === "function") {
      try { request.onload(); } catch (error) { __tw.errors.push(String(error)); }
    }
  };
  this.getAllResponseHeaders = function () { return ""; };
  this.getResponseHeader = function () { return null; };
  this.addEventListener = function () {};
  this.abort = function () {};
};
'''

#: Runs after the bootstrap: dispatch kpsdk-load/ready, configure the
#: integrity target, then call fetch and report what the patched fetch sent.
_BODY = r'''
__twDrain();
try {
  globalThis.document.dispatchEvent({ type: "kpsdk-load", target: globalThis.document });
  __twDrain();
  if (globalThis.KPSDK && typeof globalThis.KPSDK.configure === "function") {
    try { globalThis.KPSDK.configure([TW_TARGET]); } catch (error) { __tw.errors.push("configure:" + String(error)); }
  }
  globalThis.document.dispatchEvent({ type: "kpsdk-ready", target: globalThis.document });
  __twDrain();
} catch (error) {
  __tw.errors.push("events:" + String(error));
}

var __twResult = { error: null, request: null, warnings: __tw.errors.slice(0, 8) };
try {
  globalThis.fetch(TW_URL, { method: "POST", headers: TW_HEADERS, body: null, mode: "cors", credentials: "omit" });
  __twResult.request = __tw.requests.length ? __tw.requests[__tw.requests.length - 1] : null;
} catch (error) {
  __twResult.error = String(error);
}
if (!__twResult.request && !__twResult.error) {
  __twResult.error = __tw.errors.length ? __tw.errors[__tw.errors.length - 1] : "fetch-not-called";
}
return JSON.stringify(__twResult);
'''


@register_builder(AntibotEngine.TWITCH)
class TwitchIntegrityBuilder(SensorBuilder):
    name = "twitch"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        bootstrap = _bootstrap(ctx)
        headers = _headers(ctx)
        if not headers.get("x-device-id"):
            raise SensorError(
                "twitch integrity needs a device id (payload device_id)",
                context={"headers": sorted(headers)},
            )
        page_url = ctx.site_url or DEFAULT_PAGE_URL
        host = urlparse(page_url).hostname or "www.twitch.tv"
        return {
            "url": INTEGRITY_URL,
            "method": "POST",
            "headers": headers,
            "script": f"{_SHIM}\n;\n{bootstrap}\n;\n{_BODY}",
            "globals": {
                "TW_URL": INTEGRITY_URL,
                "TW_HEADERS": headers,
                "TW_TARGET": dict(KPSDK_TARGET),
                "TW_UA": str(ctx.extra.get("ua") or headers.get("user-agent") or ""),
                "TW_SITE": page_url,
                "TW_HOST": host,
                "TW_TS": ctx.timestamp_ms or int(time.time() * 1000),
                "TW_COOKIES": _cookie_header(ctx.cookies),
            },
            "bootstrap_url": str(ctx.extra.get("script_url") or KPSDK_SCRIPT_URL),
        }


def _bootstrap(ctx: BuildContext) -> str:
    for candidate in (ctx.challenge_js, ctx.extra.get("p_script")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    raise ChallengeExpiredError(
        "twitch builder needs the KPSDK bootstrap (challenge_js)",
        context={"site": ctx.site_url},
    )


def _headers(ctx: BuildContext) -> dict[str, str]:
    """Copy the envelope the solver prepared; only the client id has a default."""
    headers = {str(k).lower(): str(v) for k, v in ctx.headers.items() if v is not None}
    headers.setdefault("client-id", str(ctx.extra.get("client_id") or WEB_CLIENT_ID))
    return headers


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())
