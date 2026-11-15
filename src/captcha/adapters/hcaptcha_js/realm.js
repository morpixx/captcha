"use strict";

// jsdom realm for the hCaptcha hsw/hsj bundle: the DOM/canvas surface the
// obfuscated bundle probes before it produces the n-token or does its CTR
// crypto. Adapted from the research runner (D:/riot-research/hsw_runner_jsdom.js
// + lib/env), keeping only what the bundle itself reads: navigator/screen
// geometry, 2d canvas (node-canvas, wired by jsdom), a WebGL stub, WebCrypto,
// and the constructors its feature tests touch.
//
// Fidelity is deliberately below a real Chrome: no WebGL renderer, no font
// metrics beyond node-canvas defaults, no Chrome window-property facade. That
// is enough for the protocol and for the crypto; hardened sitekeys reject the
// fingerprint and the task belongs to the browser lane anyway.

const vm = require("node:vm");
const nodeCrypto = require("node:crypto");
const { JSDOM } = require("jsdom");

const WEBGL_VENDOR = 37445;
const WEBGL_RENDERER = 37446;
const GL_PARAMS = Object.freeze({
  [WEBGL_VENDOR]: "Google Inc. (Intel)",
  [WEBGL_RENDERER]: "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
  7936: "WebKit", // VENDOR
  7937: "WebKit WebGL", // RENDERER
  7938: "WebGL 1.0 (OpenGL ES 2.0 Chromium)", // VERSION
  35724: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)", // SHADING_LANGUAGE_VERSION
  3379: 16384, // MAX_TEXTURE_SIZE
  34076: 16384, // MAX_CUBE_MAP_TEXTURE_SIZE
  34921: 16, // MAX_VERTEX_ATTRIBS
  34930: 16, // MAX_TEXTURE_IMAGE_UNITS
  35660: 16, // MAX_VERTEX_TEXTURE_IMAGE_UNITS
  35661: 32, // MAX_COMBINED_TEXTURE_IMAGE_UNITS
  36347: 1024, // MAX_VERTEX_UNIFORM_VECTORS
  36348: 30, // MAX_VARYING_VECTORS
  36349: 1024, // MAX_FRAGMENT_UNIFORM_VECTORS
  3386: [32767, 32767], // MAX_VIEWPORT_DIMS
  33901: [1, 1024], // ALIASED_POINT_SIZE_RANGE
  33902: [1, 1024], // ALIASED_LINE_WIDTH_RANGE
});

const GL_METHODS = [
  "attachShader", "bindAttribLocation", "bindBuffer", "bindFramebuffer", "bindRenderbuffer",
  "bindTexture", "blendColor", "blendEquation", "blendEquationSeparate", "blendFunc",
  "blendFuncSeparate", "bufferData", "bufferSubData", "checkFramebufferStatus", "clear",
  "clearColor", "clearDepth", "clearStencil", "colorMask", "compileShader", "compressedTexImage2D",
  "compressedTexSubImage2D", "copyTexImage2D", "copyTexSubImage2D", "createBuffer",
  "createFramebuffer", "createProgram", "createRenderbuffer", "createShader", "createTexture",
  "cullFace", "deleteBuffer", "deleteFramebuffer", "deleteProgram", "deleteRenderbuffer",
  "deleteShader", "deleteTexture", "depthFunc", "depthMask", "depthRange", "detachShader",
  "disable", "disableVertexAttribArray", "drawArrays", "drawElements", "enable",
  "enableVertexAttribArray", "finish", "flush", "framebufferRenderbuffer", "framebufferTexture2D",
  "frontFace", "generateMipmap", "getActiveAttrib", "getActiveUniform", "getAttachedShaders",
  "getAttribLocation", "getBufferParameter", "getFramebufferAttachmentParameter",
  "getProgramInfoLog", "getRenderbufferParameter", "getShaderInfoLog", "getShaderSource",
  "getTexParameter", "getUniform", "getVertexAttrib", "getVertexAttribOffset", "hint",
  "isBuffer", "isEnabled", "isFramebuffer", "isProgram", "isRenderbuffer", "isShader", "isTexture",
  "lineWidth", "linkProgram", "pixelStorei", "polygonOffset", "readPixels", "renderbufferStorage",
  "sampleCoverage", "scissor", "shaderSource", "stencilFunc", "stencilFuncSeparate", "stencilMask",
  "stencilMaskSeparate", "stencilOp", "stencilOpSeparate", "texImage2D", "texParameterf",
  "texParameteri", "texSubImage2D", "uniform1f", "uniform1fv", "uniform1i", "uniform1iv",
  "uniform2f", "uniform2fv", "uniform2i", "uniform2iv", "uniform3f", "uniform3fv", "uniform3i",
  "uniform3iv", "uniform4f", "uniform4fv", "uniform4i", "uniform4iv", "uniformMatrix2fv",
  "uniformMatrix3fv", "uniformMatrix4fv", "useProgram", "validateProgram", "vertexAttrib1f",
  "vertexAttrib1fv", "vertexAttrib2f", "vertexAttrib2fv", "vertexAttrib3f", "vertexAttrib3fv",
  "vertexAttrib4f", "vertexAttrib4fv", "vertexAttribPointer", "viewport",
];

function define(object, name, value) {
  try {
    Object.defineProperty(object, name, { value, configurable: true, writable: true });
  } catch (_) {
    try { object[name] = value; } catch (_) { /* frozen by jsdom */ }
  }
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

function createRealm(config) {
  const dom = new JSDOM("<!DOCTYPE html><html><head></head><body></body></html>", {
    url: "https://newassets.hcaptcha.com/",
    userAgent: config.userAgent,
    pretendToBeVisual: true,
    runScripts: "outside-only",
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
  return { dom, win, ctx: vm.createContext(win, { name: "hcaptcha-bundle" }) };
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