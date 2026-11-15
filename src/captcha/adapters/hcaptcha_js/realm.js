"use strict";

// jsdom realm for the hCaptcha hsw/hsj bundle: the DOM/canvas surface it probes
// before producing the n-token or doing its CTR crypto. Adapted from the
// research runner (D:/riot-research/hsw_runner_jsdom.js + lib/env), keeping
// only what the bundle reads: navigator/screen geometry, 2d canvas
// (node-canvas, wired by jsdom), a WebGL stub, WebCrypto, fetch.
//
// Fidelity is deliberately below Chrome: no real WebGL, no font stack, no
// window-property facade. Hardened sitekeys reject the fingerprint; that task
// belongs to the browser lane.

const nodeCrypto = require("node:crypto");
const { JSDOM, VirtualConsole } = require("jsdom");

const GL_PARAMS = Object.freeze({
  37445: "Google Inc. (Intel)", // UNMASKED_VENDOR_WEBGL
  37446: "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)", // UNMASKED_RENDERER_WEBGL
  7936: "WebKit", 7937: "WebKit WebGL", 7938: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
  35724: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
  3379: 16384, 34076: 16384, 34921: 16, 34930: 16, 35660: 16, 35661: 32,
  36347: 1024, 36348: 30, 36349: 1024,
  3386: [32767, 32767], 33901: [1, 1024], 33902: [1, 1024],
});

// Every WebGL1 entry point the fingerprint probe reaches for.
const GL_METHODS = (
  "attachShader bindAttribLocation bindBuffer bindFramebuffer bindRenderbuffer bindTexture blendColor  " +
  "blendEquation blendEquationSeparate blendFunc blendFuncSeparate bufferData bufferSubData  " +
  "checkFramebufferStatus clear clearColor clearDepth clearStencil colorMask compileShader  " +
  "compressedTexImage2D compressedTexSubImage2D copyTexImage2D copyTexSubImage2D createBuffer  " +
  "createFramebuffer createProgram createRenderbuffer createShader createTexture cullFace deleteBuffer  " +
  "deleteFramebuffer deleteProgram deleteRenderbuffer deleteShader deleteTexture depthFunc depthMask  " +
  "depthRange detachShader disable disableVertexAttribArray drawArrays drawElements enable  " +
  "enableVertexAttribArray finish flush framebufferRenderbuffer framebufferTexture2D frontFace  " +
  "generateMipmap getActiveAttrib getActiveUniform getAttachedShaders getAttribLocation getBufferParameter  " +
  "getFramebufferAttachmentParameter getProgramInfoLog getRenderbufferParameter getShaderInfoLog  " +
  "getShaderSource getTexParameter getUniform getVertexAttrib getVertexAttribOffset hint isBuffer isEnabled  " +
  "isFramebuffer isProgram isRenderbuffer isShader isTexture lineWidth linkProgram pixelStorei  " +
  "polygonOffset readPixels renderbufferStorage sampleCoverage scissor shaderSource stencilFunc  " +
  "stencilFuncSeparate stencilMask stencilMaskSeparate stencilOp stencilOpSeparate texImage2D texParameterf  " +
  "texParameteri texSubImage2D uniform1f uniform1fv uniform1i uniform1iv uniform2f uniform2fv uniform2i  " +
  "uniform2iv uniform3f uniform3fv uniform3i uniform3iv uniform4f uniform4fv uniform4i uniform4iv  " +
  "uniformMatrix2fv uniformMatrix3fv uniformMatrix4fv useProgram validateProgram vertexAttrib1f  " +
  "vertexAttrib1fv vertexAttrib2f vertexAttrib2fv vertexAttrib3f vertexAttrib3fv vertexAttrib4f  " +
  "vertexAttrib4fv vertexAttribPointer viewport "
).trim().split(" ");

function define(object, name, value) {
  try { Object.defineProperty(object, name, { value, configurable: true, writable: true }); }
  catch (_) { try { object[name] = value; } catch (_) { /* frozen by jsdom */ } }
}

function makeWebGLStub(canvas) {
  const context = {
    canvas,
    drawingBufferWidth: canvas.width || 1,
    drawingBufferHeight: canvas.height || 1,
    getParameter: (param) => (param in GL_PARAMS ? GL_PARAMS[param] : 0),
    getExtension: () => null,
    getSupportedExtensions: () => [],
    getContextAttributes: () => ({
      alpha: true, antialias: true, depth: true, failIfMajorPerformanceCaveat: false,
      premultipliedAlpha: true, preserveDrawingBuffer: false, stencil: true,
    }),
    getShaderPrecisionFormat: () => ({ precision: 23, rangeMax: 127, rangeMin: 127 }),
    getError: () => 0,
    isContextLost: () => false,
    getUniformLocation: () => ({}),
    getAttribLocation: () => -1,
    getProgramParameter: () => true,
    getShaderParameter: () => true,
    getProgramInfoLog: () => "",
    getShaderInfoLog: () => "",
  };
  for (const name of GL_METHODS) {
    if (!(name in context)) context[name] = () => null;
  }
  return context;
}

function installCanvas(win) {
  const proto = win.HTMLCanvasElement && win.HTMLCanvasElement.prototype;
  if (!proto) return;
  const nativeGetContext = proto.getContext;
  const stubs = new WeakMap();
  proto.getContext = function getContext(kind, attrs) {
    if (kind === "webgl" || kind === "webgl2" || kind === "experimental-webgl") {
      let stub = stubs.get(this);
      if (!stub) {
        stub = makeWebGLStub(this);
        stubs.set(this, stub);
      }
      return stub;
    }
    return nativeGetContext.call(this, kind, attrs);
  };
}

function installNavigator(win, config) {
  const nav = win.navigator;
  // jsdom normalises the "userAgent" option on some builds; pin what the
  // checksiteconfig/getcaptcha request actually claims.
  define(nav, "userAgent", config.userAgent);
  define(nav, "appVersion", config.userAgent.replace(/^Mozilla\//, ""));
  define(nav, "hardwareConcurrency", config.hardwareConcurrency);
  define(nav, "deviceMemory", 8);
  define(nav, "maxTouchPoints", config.maxTouchPoints);
  define(nav, "platform", config.platform);
  define(nav, "vendor", "Google Inc.");
  define(nav, "webdriver", false);
  define(nav, "language", "en-US");
  define(nav, "languages", ["en-US", "en"]);
  define(nav, "userAgentData", {
    brands: [
      { brand: "Chromium", version: "131" },
      { brand: "Google Chrome", version: "131" },
      { brand: "Not_A Brand", version: "24" },
    ],
    mobile: false,
    platform: "Windows",
    getHighEntropyValues: async () => ({}),
  });
  define(nav, "plugins", nav.plugins);
  define(nav, "mimeTypes", nav.mimeTypes);
}

function installScreen(win, config) {
  const screen = win.screen;
  const width = config.screenWidth;
  const height = config.screenHeight;
  for (const [name, value] of [
    ["width", width],
    ["height", height],
    ["availWidth", width],
    ["availHeight", height - 40],
    ["colorDepth", 24],
    ["pixelDepth", 24],
    ["availLeft", 0],
    ["availTop", 0],
    ["orientation", { angle: 0, type: "landscape-primary", addEventListener() {} }],
  ]) {
    define(screen, name, value);
  }
  for (const [name, value] of [
    ["devicePixelRatio", 1],
    ["innerWidth", width],
    ["innerHeight", height - 120],
    ["outerWidth", width],
    ["outerHeight", height - 40],
    ["screenX", 0],
    ["screenY", 0],
    ["scrollX", 0],
    ["scrollY", 0],
  ]) {
    define(win, name, value);
  }
}

function installGlobals(win) {
  define(win, "WebAssembly", WebAssembly);
  define(win, "crypto", nodeCrypto.webcrypto);
  define(win, "atob", (value) => Buffer.from(String(value), "base64").toString("binary"));
  define(win, "btoa", (value) => Buffer.from(String(value), "binary").toString("base64"));
  define(win, "fetch", (...args) => globalThis.fetch(...args));
  define(win, "chrome", { app: {}, csi: () => ({}), loadTimes: () => ({}), runtime: {} });
  if (typeof win.queueMicrotask !== "function") {
    define(win, "queueMicrotask", (fn) => Promise.resolve().then(fn));
  }
  for (const name of [
    "MessageChannel", "MessagePort", "BroadcastChannel", "Blob", "File", "FormData",
    "Headers", "Request", "Response", "TextEncoder", "TextDecoder", "structuredClone",
  ]) {
    if (win[name] === undefined && globalThis[name] !== undefined) {
      define(win, name, globalThis[name]);
    }
  }
}

// The bundle is chatty: Steam's hsw logs "undefined" after every solve. jsdom's
// default virtual console forwards window.console straight to Node's stdout,
// which is the worker's protocol channel, so the next request reads that line as
// a response. Console output is swallowed by default; HCAPTCHA_JS_DEBUG routes it
// to stderr, where it cannot corrupt the protocol.
function createVirtualConsole() {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (err) => {
    const detail = err && err.stack ? err.stack : String(err);
    process.stderr.write("[hcaptcha-js] jsdomError: " + detail + "\n");
  });
  if (!process.env.HCAPTCHA_JS_DEBUG) return virtualConsole;
  for (const event of ["log", "info", "warn", "error", "dir", "debug"]) {
    virtualConsole.on(event, (...args) => {
      const text = args
        .map((arg) => {
          try {
            return typeof arg === "string" ? arg : JSON.stringify(arg);
          } catch (_) {
            return String(arg);
          }
        })
        .join(" ");
      process.stderr.write("[hcaptcha-js bundle] " + text + "\n");
    });
  }
  return virtualConsole;
}

function createRealm(config) {
  const dom = new JSDOM("<!DOCTYPE html><html><head></head><body></body></html>", {
    url: "https://newassets.hcaptcha.com/",
    userAgent: config.userAgent,
    pretendToBeVisual: true,
    // The bundle's wasm glue needs the window's own realm and event loop, so
    // jsdom has to set one up ("outside-only"). We then evaluate the bundle in
    // *that* context (dom.getInternalVMContext below); a second
    // vm.createContext(win) loses the "window" global binding and the bundle
    // aborts with a wasm "unreachable" trap.
    runScripts: "outside-only",
    virtualConsole: createVirtualConsole(),
  });
  const win = dom.window;
  installCanvas(win);
  installNavigator(win, config);
  installScreen(win, config);
  installGlobals(win);
  define(win, "globalThis", win);
  define(win, "self", win);
  define(win, "top", win);
  define(win, "parent", win);
  define(win, "frames", win);
  return { dom, win, ctx: dom.getInternalVMContext() };
}

function whenReady(win) {
  if (win.document && win.document.readyState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    try { win.addEventListener("load", finish, { once: true }); } catch (_) { /* noop */ }
    try { win.setTimeout(finish, 0); } catch (_) { finish(); }
  });
}

module.exports = { createRealm, whenReady };