/* Minimal browser surface for the tenant's bmak script (engines/akamai/builder.py).
 *
 * Not a DOM: the smallest set of globals that lets a sensor script collect its
 * fields and issue its sensor POST, plus a recorder that keeps request bodies
 * instead of sending them. No network, no timers that outlive the call, no
 * canvas pixels -- a script that needs more lands in the errors array, which the
 * builder turns into SensorError rather than a fabricated payload. Writes to
 * window land on globalThis, so window.KPSDK / window.bmak still work.
 */

var __captured = [];
var __errors = [];
var __self = this;

function __record(url, method, body, headers) {
  var text = '';
  if (body !== null && body !== undefined) {
    if (typeof body === 'string') {
      text = body;
    } else if (typeof body.toString === 'function') {
      text = body.toString();
    }
  }
  __captured.push({
    url: String(url === null || url === undefined ? '' : url),
    method: String(method === null || method === undefined ? 'GET' : method).toUpperCase(),
    body: text,
    headers: headers || {},
  });
}

function __events() {
  return {
    addEventListener: function () {}, removeEventListener: function () {},
    dispatchEvent: function () { return true; },
    attachEvent: function () {}, detachEvent: function () {},
  };
}

function __canvas_context() {
  var ctx = { canvas: null, fillStyle: '#000', strokeStyle: '#000', font: '10px sans-serif', globalAlpha: 1, lineWidth: 1, textBaseline: 'alphabetic', textAlign: 'start' };
  var ops = ['fillRect', 'clearRect', 'strokeRect', 'beginPath', 'closePath', 'moveTo', 'lineTo', 'arc',
    'rect', 'fill', 'stroke', 'save', 'restore', 'translate', 'rotate', 'scale', 'setTransform',
    'fillText', 'strokeText', 'drawImage', 'quadraticCurveTo', 'bezierCurveTo', 'clip', 'ellipse', 'arcTo'];
  for (var i = 0; i < ops.length; i++) {
    (function (name) {
      ctx[name] = function () {};
    })(ops[i]);
  }
  ctx.measureText = function (value) { return { width: String(value || '').length * 7 }; };
  ctx.getImageData = function (x, y, w, h) { return { data: new Uint8ClampedArray(4), width: w || 1, height: h || 1 }; };
  ctx.putImageData = function () {};
  ctx.createLinearGradient = function () { return { addColorStop: function () {} }; };
  ctx.createRadialGradient = ctx.createLinearGradient;
  return ctx;
}

function __webgl_context() {
  var ctx = {
    canvas: null,
    drawingBufferWidth: 300,
    drawingBufferHeight: 150,
    getParameter: function (name) { return String(name) === '37445' ? 'Google Inc.' : 'ANGLE (Software)'; },
    getExtension: function () { return null; },
    getSupportedExtensions: function () { return []; },
    getShaderPrecisionFormat: function () { return { precision: 23, rangeMin: 127, rangeMax: 127 }; },
  };
  var ops = ['bindBuffer', 'bufferData', 'clear', 'clearColor', 'createBuffer', 'createProgram',
    'createShader', 'drawArrays', 'enable', 'getAttribLocation', 'getUniformLocation', 'linkProgram',
    'shaderSource', 'compileShader', 'viewport', 'useProgram', 'deleteShader', 'deleteBuffer'];
  for (var i = 0; i < ops.length; i++) {
    (function (name) {
      ctx[name] = function () { return {}; };
    })(ops[i]);
  }
  return ctx;
}

function __element(tag) {
  var el = __events();
  el.tagName = String(tag === null || tag === undefined ? 'div' : tag).toUpperCase();
  el.nodeName = el.tagName;
  el.nodeType = 1;
  el.style = {};
  el.dataset = {};
  el.childNodes = [];
  el.children = [];
  el.attributes = {};
  el.className = '';
  el.id = '';
  el.width = 300;
  el.height = 150;
  el.innerHTML = '';
  el.textContent = '';
  el.src = '';
  el.href = '';
  el.getContext = function (kind) {
    return String(kind).indexOf('webgl') === 0 ? __webgl_context() : __canvas_context();
  };
  el.appendChild = function (child) { el.childNodes.push(child); return child; };
  el.removeChild = function (child) { return child; };
  el.insertBefore = function (child) { return child; };
  el.setAttribute = function (key, value) { el.attributes[String(key)] = String(value); };
  el.getAttribute = function (key) {
    var name = String(key);
    return Object.prototype.hasOwnProperty.call(el.attributes, name) ? el.attributes[name] : null;
  };
  el.removeAttribute = function (key) { delete el.attributes[String(key)]; };
  el.getBoundingClientRect = function () {
    return { top: 0, left: 0, right: 300, bottom: 150, width: 300, height: 150, x: 0, y: 0 };
  };
  el.querySelector = function () { return null; };
  el.querySelectorAll = function () { return []; };
  el.getElementsByTagName = function () { return []; };
  el.toDataURL = function () { return 'data:image/png;base64,'; };
  el.focus = function () {};
  el.blur = function () {};
  el.click = function () {};
  el.cloneNode = function () { return __element(tag); };
  return el;
}

function __location(href) {
  var parts = String(href || 'https://example.test/').split('://');
  var rest = parts.length > 1 ? parts[1] : parts[0];
  var slash = rest.indexOf('/');
  var host = slash < 0 ? rest : rest.slice(0, slash);
  var path = slash < 0 ? '/' : rest.slice(slash);
  return {
    href: String(href || 'https://example.test/'),
    protocol: (parts.length > 1 ? parts[0] : 'https') + ':',
    host: host,
    hostname: host.split(':')[0],
    port: '',
    pathname: path.split('?')[0],
    search: path.indexOf('?') < 0 ? '' : path.slice(path.indexOf('?')),
    hash: '',
    origin: (parts.length > 1 ? parts[0] : 'https') + '://' + host,
    reload: function () {}, assign: function () {}, replace: function () {},
    toString: function () { return this.href; },
  };
}

function __install(href) {
  var doc = __events();
  doc.readyState = 'complete';
  doc.cookie = '';
  doc.documentElement = __element('html');
  doc.body = __element('body');
  doc.head = __element('head');
  doc.title = ''; doc.referrer = '';
  doc.URL = String(href || 'https://example.test/');
  doc.domain = 'example.test';
  doc.hidden = false; doc.visibilityState = 'visible';
  doc.createElement = function (tag) { return __element(tag); };
  doc.createElementNS = function (ns, tag) { return __element(tag); };
  doc.createTextNode = function (text) { return { textContent: String(text), nodeType: 3 }; };
  doc.getElementById = function () { return null; };
  doc.getElementsByTagName = function () { return []; };
  doc.getElementsByClassName = function () { return []; };
  doc.querySelector = function () { return null; };
  doc.querySelectorAll = function () { return []; };
  doc.hasFocus = function () { return true; };

  var nav = __events();
  nav.userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
  nav.appVersion = nav.userAgent.replace('Mozilla/', '');
  nav.platform = 'Win32';
  nav.vendor = 'Google Inc.';
  nav.language = 'en-US';
  nav.languages = ['en-US', 'en'];
  nav.hardwareConcurrency = 8;
  nav.deviceMemory = 8;
  nav.maxTouchPoints = 0;
  nav.cookieEnabled = true;
  nav.doNotTrack = null;
  nav.onLine = true;
  nav.webdriver = false;
  nav.plugins = { length: 5, item: function () { return null; }, namedItem: function () { return null; } };
  nav.mimeTypes = { length: 2, item: function () { return null; }, namedItem: function () { return null; } };
  nav.sendBeacon = function (url, body) { __record(url, 'POST', body, {}); return true; };
  nav.connection = { effectiveType: '4g', rtt: 50, downlink: 10, saveData: false };

  var screen = {
    width: 1920, height: 1080, availWidth: 1920, availHeight: 1040,
    colorDepth: 24, pixelDepth: 24, availLeft: 0, availTop: 0,
    orientation: { type: 'landscape-primary', angle: 0, addEventListener: function () {} },
  };

  var storage = {
    length: 0,
    getItem: function () { return null; }, setItem: function () {},
    removeItem: function () {}, clear: function () {}, key: function () { return null; },
  };

  var crypto = {
    getRandomValues: function (view) {
      for (var i = 0; i < view.length; i++) { view[i] = (i * 37 + 11) & 255; }
      return view;
    },
  };

  var performance = __events();
  performance.timing = { navigationStart: Date.now() - 5000 };
  performance.now = function () { return Date.now() % 100000; };
  performance.timeOrigin = Date.now() - 5000;
  performance.mark = function () {};
  performance.getEntriesByType = function () { return []; };
  performance.getEntriesByName = function () { return []; };

  var win = __self || {};
  win.document = doc;
  win.navigator = nav;
  win.screen = screen;
  win.location = __location(href);
  win.history = { length: 1, pushState: function () {}, replaceState: function () {}, back: function () {} };
  win.localStorage = storage;
  win.sessionStorage = storage;
  win.crypto = crypto;
  win.performance = performance;
  win.innerWidth = 1920; win.innerHeight = 937;
  win.outerWidth = 1920; win.outerHeight = 1080;
  win.devicePixelRatio = 1;
  win.screenX = 0; win.screenY = 0;
  win.pageXOffset = 0; win.pageYOffset = 0; win.scrollX = 0; win.scrollY = 0;
  win.name = ''; win.closed = false;
  win.self = win; win.top = win; win.parent = win; win.frames = win;
  win.window = win; win.globalThis = win;
  win.setTimeout = function (fn) { return 1; }; win.setInterval = function (fn) { return 1; };
  win.clearTimeout = function () {}; win.clearInterval = function () {};
  win.requestAnimationFrame = function () { return 1; }; win.cancelAnimationFrame = function () {};
  win.matchMedia = function () {
    return { matches: false, media: '', addListener: function () {}, removeListener: function () {}, addEventListener: function () {} };
  };
  win.getComputedStyle = function () {
    return { getPropertyValue: function () { return ''; }, width: '300px', height: '150px' };
  };
  win.addEventListener = function () {}; win.removeEventListener = function () {};
  win.dispatchEvent = function () { return true; };
  win.postMessage = function () {}; win.focus = function () {};
  win.open = function () { return win; }; win.close = function () {};
  win.atob = function (value) { return __atob(String(value)); };
  win.btoa = function (value) { return __btoa(String(value)); };
  win.alert = function () {};
  win.Image = function () { return __element('img'); };
  win.XMLHttpRequest = __xhr;
  win.fetch = __fetch;
  win.MutationObserver = function () {
    this.observe = function () {};
    this.disconnect = function () {};
    this.takeRecords = function () { return []; };
  };
  win.IntersectionObserver = win.MutationObserver;
  win.ResizeObserver = win.MutationObserver;
  win.HTMLElement = function () {};
  win.Element = win.HTMLElement;
  win.Node = win.HTMLElement;
  win.Event = function (type) {
    return { type: String(type), preventDefault: function () {}, stopPropagation: function () {} };
  };
  win.EventTarget = function () {};
  win.CustomEvent = win.Event;
  win.Worker = function () {
    this.postMessage = function () {};
    this.terminate = function () {};
    this.addEventListener = function () {};
  };
  win.TextEncoder = function () {
    this.encode = function (text) {
      var out = [];
      var value = String(text);
      for (var i = 0; i < value.length; i++) {
        var code = value.charCodeAt(i);
        if (code < 128) { out.push(code); }
        else if (code < 2048) { out.push(192 | (code >> 6), 128 | (code & 63)); }
        else { out.push(224 | (code >> 12), 128 | ((code >> 6) & 63), 128 | (code & 63)); }
      }
      return new Uint8Array(out);
    };
  };
  win.TextDecoder = function () {
    this.decode = function (bytes) {
      var out = '';
      for (var i = 0; i < bytes.length; i++) { out += String.fromCharCode(bytes[i]); }
      return out;
    };
  };
  return win;
}

function __btoa(value) {
  var table = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  var out = '';
  for (var i = 0; i < value.length; i += 3) {
    var a = value.charCodeAt(i) & 255;
    var b = i + 1 < value.length ? value.charCodeAt(i + 1) & 255 : 0;
    var c = i + 2 < value.length ? value.charCodeAt(i + 2) & 255 : 0;
    out += table[a >> 2] + table[((a & 3) << 4) | (b >> 4)];
    out += i + 1 < value.length ? table[((b & 15) << 2) | (c >> 6)] : '=';
    out += i + 2 < value.length ? table[c & 63] : '=';
  }
  return out;
}

function __atob(value) {
  var table = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  var clean = String(value).replace(/[^A-Za-z0-9+/=]/g, '');
  var out = '';
  for (var i = 0; i < clean.length; i += 4) {
    var n = 0;
    var pad = 0;
    for (var j = 0; j < 4; j++) {
      var ch = clean.charAt(i + j);
      var digit = table.indexOf(ch);
      if (ch === '=' || digit < 0) { pad += 1; digit = 0; }
      n = (n << 6) | digit;
    }
    out += String.fromCharCode((n >> 16) & 255);
    if (pad < 2) { out += String.fromCharCode((n >> 8) & 255); }
    if (pad < 1) { out += String.fromCharCode(n & 255); }
  }
  return out;
}

function __xhr() {
  var self = this;
  self.readyState = 0;
  self.status = 0;
  self.statusText = '';
  self.responseText = '';
  self.response = '';
  self.responseType = '';
  self.timeout = 0;
  self.withCredentials = false;
  self.headers = {};
  self.onreadystatechange = null;
  self.onload = null;
  self.onerror = null;
  self.ontimeout = null;
  self.onabort = null;
  self.open = function (method, url) {
    self.method = method;
    self.url = url;
    self.readyState = 1;
  };
  self.setRequestHeader = function (key, value) {
    self.headers[String(key)] = String(value);
  };
  self.getResponseHeader = function () { return null; };
  self.getAllResponseHeaders = function () { return ''; };
  self.abort = function () {};
  self.send = function (body) {
    __record(self.url, self.method, body, self.headers);
    self.readyState = 4;
    self.status = 200;
    self.statusText = 'OK';
    self.responseText = '{}';
    self.response = '{}';
    try {
      if (typeof self.onreadystatechange === 'function') { self.onreadystatechange(); }
      if (typeof self.onload === 'function') { self.onload(); }
    } catch (e) {
      __errors.push(String(e));
    }
  };
}

function __fetch(url, options) {
  var opts = options || {};
  var headers = {};
  var raw = opts.headers || {};
  for (var key in raw) {
    if (Object.prototype.hasOwnProperty.call(raw, key)) { headers[key] = String(raw[key]); }
  }
  __record(url, opts.method || 'GET', opts.body, headers);
  var body = '{}';
  return {
    ok: true,
    status: 200,
    text: function () { return body; },
    json: function () { return {}; },
    headers: { get: function () { return null; }, has: function () { return false; } },
  };
}

function __run(source) {
  try {
    eval(String(source));
  } catch (e) {
    __errors.push(String(e));
  }
}

function __finish() {
  return JSON.stringify({ sent: __captured, errors: __errors.slice(0, 8) });
}
