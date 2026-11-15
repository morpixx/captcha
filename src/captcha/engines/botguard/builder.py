"""Google BotGuard sensor builder.

BotGuard is a bytecode VM: the target hands out an interpreter script plus a
program blob, and the VM returns a response token that proves a browser
executed it. The interpreter is product-scoped (reCAPTCHA, WAA/YouTube,
account sign-in), so the builder takes both halves from the payload and
keeps no per-product bytecode of its own.

The builder only composes the driver: the shim the VM probes, then the
interpreter, then the snapshot call. The solver runs it in ctx.js_runtime
and ships the token. Everything the VM can use to recognise a sandbox is
deliberately present but deterministic -- a random shim would change the
token on every retry and make replay impossible.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, SensorError, UnsolvableError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder

#: reCAPTCHA's challenge page and the verify endpoint that consumes the token.
RECAPTCHA_BFRAME_URL: str = "https://www.google.com/recaptcha/api2/bframe"
RECAPTCHA_RELOAD_URL: str = "https://www.google.com/recaptcha/api2/reload"
RECAPTCHA_USERVERIFY_URL: str = "https://www.google.com/recaptcha/api2/userverify"

#: Google Web Anti-Abuse API (public, documented by luanrt/bgutils).
WAA_CREATE_URL: str = "https://jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa/Create"
WAA_GENERATE_IT_URL: str = "https://jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa/GenerateIT"
WAA_API_KEY: str = "AIzaSyDyT5W0Jh49F30Pqqtyfdf7pDLFKLJoAnw"

#: Interpreter scripts are served from the Google host the challenge named.
GOOGLE_HOST: str = "https://www.google.com"

#: How this builder expects the token claim to be named on the page.
DEFAULT_GLOBAL_NAME: str = "botguard"

#: Everything the VM probes before it will produce a token.
_SHIM = r'''
var __bg = { timers: [], errors: [], token: null };

function __bgEl(tag) {
  return {
    tagName: tag ? String(tag).toUpperCase() : "DIV",
    style: {}, dataset: {}, attributes: {},
    setAttribute: function (name, value) { this.attributes[name] = String(value); },
    getAttribute: function (name) { return this.attributes[name] === undefined ? null : this.attributes[name]; },
    appendChild: function (child) { return child; },
    removeChild: function (child) { return child; },
    addEventListener: function () {},
    removeEventListener: function () {},
    remove: function () {},
    click: function () {},
    focus: function () {},
    getContext: function () { return null; },
    toDataURL: function () { return "data:,"; },
    textContent: "", innerHTML: "", id: "", src: "", type: "", nonce: ""
  };
}

function __bgDrain() {
  var budget = 512;
  while (__bg.timers.length && budget-- > 0) {
    var item = __bg.timers.shift();
    try { item[0].call(globalThis, BG_TIMESTAMP); } catch (error) { __bg.errors.push(String(error)); }
  }
}

if (typeof globalThis.window === "undefined") { globalThis.window = globalThis; }
if (typeof globalThis.self === "undefined") { globalThis.self = globalThis; }
if (typeof globalThis.top === "undefined") { globalThis.top = globalThis; }
if (typeof globalThis.parent === "undefined") { globalThis.parent = globalThis; }

globalThis.navigator = {
  userAgent: BG_UA, appVersion: BG_UA, platform: "Win32", vendor: "Google Inc.",
  language: "en-US", languages: ["en-US", "en"], webdriver: false,
  hardwareConcurrency: 8, deviceMemory: 8, plugins: [], mimeTypes: [], maxTouchPoints: 0,
  cookieEnabled: true, doNotTrack: null, onLine: true
};
globalThis.screen = {
  width: 1920, height: 1080, availWidth: 1920, availHeight: 1040,
  colorDepth: 24, pixelDepth: 24, orientation: { type: "landscape-primary", angle: 0 }
};
globalThis.location = {
  href: BG_SITE, protocol: "https:", host: BG_HOST, hostname: BG_HOST,
  origin: "https://" + BG_HOST, pathname: "/", search: "", hash: "",
  reload: function () {}, assign: function () {}, replace: function () {}
};
globalThis.performance = {
  now: function () { return BG_TIMESTAMP; },
  timeOrigin: BG_TIMESTAMP,
  getEntriesByType: function () { return []; },
  mark: function () {},
  measure: function () {}
};
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
  readyState: "complete", cookie: BG_COOKIES, referrer: BG_SITE, title: "",
  visibilityState: "visible", hidden: false,
  hasFocus: function () { return true; },
  createElement: function (tag) { return __bgEl(tag); },
  createTextNode: function (text) { return { textContent: String(text) }; },
  getElementById: function () { return null; },
  getElementsByTagName: function () { return []; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
  removeEventListener: function () {},
  dispatchEvent: function () { return true; },
  head: __bgEl("head"), body: __bgEl("body"), documentElement: __bgEl("html")
};

globalThis.setTimeout = function (fn, delay) { __bg.timers.push([fn, delay || 0]); return __bg.timers.length; };
globalThis.setInterval = function () { return 0; };
globalThis.clearTimeout = function () {};
globalThis.clearInterval = function () {};
globalThis.requestAnimationFrame = function (fn) { __bg.timers.push([fn, 0]); return __bg.timers.length; };
globalThis.cancelAnimationFrame = function () {};
globalThis.addEventListener = function () {};
globalThis.removeEventListener = function () {};

if (typeof globalThis.TextEncoder === "undefined") {
  globalThis.TextEncoder = function () {
    this.encode = function (text) {
      var bytes = unescape(encodeURIComponent(String(text)));
      var out = new Uint8Array(bytes.length);
      for (var i = 0; i < bytes.length; i++) { out[i] = bytes.charCodeAt(i); }
      return out;
    };
  };
}

var __BG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
globalThis.btoa = function (input) {
  var text = String(input), out = "", index = 0;
  while (index < text.length) {
    var a = text.charCodeAt(index++) & 255;
    var b = index < text.length ? text.charCodeAt(index++) & 255 : NaN;
    var c = index < text.length ? text.charCodeAt(index++) & 255 : NaN;
    out += __BG_ALPHABET.charAt(a >> 2);
    out += __BG_ALPHABET.charAt(((a & 3) << 4) | (isNaN(b) ? 0 : b >> 4));
    out += isNaN(b) ? "=" : __BG_ALPHABET.charAt(((b & 15) << 2) | (isNaN(c) ? 0 : c >> 6));
    out += isNaN(c) ? "=" : __BG_ALPHABET.charAt(c & 63);
  }
  return out;
};
globalThis.atob = function (input) {
  var text = String(input).replace(/[^A-Za-z0-9+/=]/g, ""), out = "", index = 0;
  while (index < text.length) {
    var a = __BG_ALPHABET.indexOf(text.charAt(index++));
    var b = __BG_ALPHABET.indexOf(text.charAt(index++));
    var c = __BG_ALPHABET.indexOf(text.charAt(index++));
    var d = __BG_ALPHABET.indexOf(text.charAt(index++));
    out += String.fromCharCode((a << 2) | (b >> 4));
    if (c !== -1) { out += String.fromCharCode(((b & 15) << 4) | (c >> 2)); }
    if (d !== -1) { out += String.fromCharCode(((c & 3) << 6) | d); }
  }
  return out;
};
'''

#: Calls the VM: vm.a(program, ...) for the WAA/YouTube shape, the
#: botguard.bg() constructor for reCAPTCHA's older shape.
_BODY = r'''
__bgDrain();

var __bgResult = { token: null, error: null, stage: "start" };
function __bgCapture(value) {
  if (typeof value === "string" && value.length > 0) { __bgResult.token = value; }
}

var __bgVm = null;
if (BG_GLOBAL) {
  try {
    __bgVm = eval("typeof " + BG_GLOBAL + " !== 'undefined' ? " + BG_GLOBAL + " : null");
  } catch (error) {
    __bg.errors.push("lookup:" + String(error));
  }
  if (!__bgVm && globalThis[BG_GLOBAL]) { __bgVm = globalThis[BG_GLOBAL]; }
}
if (!__bgVm && typeof botguard !== "undefined") { __bgVm = botguard; }
if (!__bgVm) {
  __bgResult.error = "vm-missing";
  __bgResult.stage = "lookup";
  __bgResult.warnings = __bg.errors.slice(0, 8);
  return JSON.stringify(__bgResult);
}

try {
  if (typeof __bgVm.a === "function") {
    __bgResult.stage = "vm.a";
    var __bgSnapshot = null;
    var __bgSetup = __bgVm.a(
      BG_PROGRAM,
      function (fn) { __bgSnapshot = fn; },
      true,
      undefined,
      function () {},
      [[], []],
      undefined,
      false,
      undefined
    );
    if (__bgSetup && typeof __bgSetup[0] === "function") { __bgSnapshot = __bgSetup[0]; }
    if (typeof __bgSnapshot !== "function") {
      __bgResult.error = "snapshot-missing";
    } else {
      __bgResult.stage = "snapshot";
      var __bgReturn = __bgSnapshot(function (response) { __bgCapture(response); }, [[], [], [], false]);
      if (!__bgResult.token) { __bgCapture(__bgReturn); }
      if (!__bgResult.token) { __bgResult.error = "no-token"; }
    }
  } else if (typeof __bgVm.bg === "function") {
    __bgResult.stage = "bg";
    var __bgInstance = new __bgVm.bg(BG_PROGRAM);
    var __bgFunctions = __bgInstance && __bgInstance[0] ? __bgInstance : null;
    if (__bgFunctions) {
      try { __bgCapture(__bgFunctions[0](__bgFunctions[1], undefined)); } catch (error) { __bg.errors.push("bg-call:" + String(error)); }
      if (!__bgResult.token) {
        try { __bgCapture(__bgFunctions[0].call(__bgFunctions)); } catch (error) { __bg.errors.push("bg-bare:" + String(error)); }
      }
    }
    if (!__bgResult.token) { __bgResult.error = "bg-api-unsupported"; }
  } else if (typeof __bgVm === "function") {
    __bgResult.stage = "factory";
    var __bgMade = new __bgVm(BG_PROGRAM);
    __bgCapture(__bgMade);
    if (!__bgResult.token) { __bgResult.error = "factory-api-unsupported"; }
  } else {
    __bgResult.error = "vm-shape-unknown";
  }
} catch (error) {
  __bgResult.error = "vm-throw:" + String(error);
}
__bgDrain();
if (__bgResult.error && __bgResult.stage === "snapshot" && __bgResult.token) { __bgResult.error = null; }
__bgResult.warnings = __bg.errors.slice(0, 8);
return JSON.stringify(__bgResult);
'''


@register_builder(AntibotEngine.BOTGUARD)
class BotGuardBuilder(SensorBuilder):
    name = "botguard"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        program = ctx.extra.get("program") or ctx.extra.get("ftx")
        if not isinstance(program, str) or not program.strip():
            raise ChallengeExpiredError(
                "botguard needs the f.txt program; it rotates with the challenge",
                context={"site": ctx.site_url},
            )
        interpreter = ctx.extra.get("interpreter_js") or ctx.challenge_js
        if not isinstance(interpreter, str) or not interpreter.strip():
            raise UnsolvableError(
                "botguard needs the interpreter script (interpreter_js or an interpreter_url)",
                context={"site": ctx.site_url},
            )
        global_name = str(ctx.extra.get("global_name") or "").strip()
        if global_name and not global_name.isidentifier():
            raise SensorError(
                "botguard global_name must be a plain JS identifier",
                context={"global_name": global_name[:80]},
            )
        site_url = ctx.site_url or RECAPTCHA_BFRAME_URL
        host = urlparse(site_url).hostname or "www.google.com"
        return {
            "script": f"{_SHIM}\n;\n{interpreter}\n;\n{_BODY}",
            "globals": {
                "BG_PROGRAM": program,
                "BG_GLOBAL": global_name,
                "BG_UA": str(ctx.extra.get("ua") or ""),
                "BG_SITE": site_url,
                "BG_HOST": host,
                "BG_TIMESTAMP": ctx.timestamp_ms or 0,
                "BG_COOKIES": _cookie_header(ctx.cookies),
            },
            "product": str(ctx.extra.get("product") or "recaptcha"),
            "global_name": global_name or DEFAULT_GLOBAL_NAME,
        }


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())
