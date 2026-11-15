"use strict";

// Loads and runs the hCaptcha hsw/hsj bundle.
//
// The JWT from checksiteconfig carries the bundle name ("n") and its asset path
// ("l"); the bundle is fetched from newassets.hcaptcha.com, cached on disk for
// 12h, then evaluated once per realm. Realms are keyed by build URL + client
// fingerprint and reused across tasks, the way a page reuses its own script.
//
// The exported function has two shapes: hsw(reqJwt, options) -> n-token string,
// and hsw(mode, bytes) with mode 1 = encrypt, 0 = decrypt, used for the
// msgpack body and the encrypted getcaptcha response.

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const nodeCrypto = require("node:crypto");
const { createRealm, whenReady } = require("./realm");

const CACHE_DIR = path.join(__dirname, ".cache");
const CACHE_MAX_AGE_MS = 12 * 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 30_000;
const MAX_BUNDLE_BYTES = 10 * 1024 * 1024;
const RUN_TIMEOUT_MS = 60_000;
const MAX_REALMS = 4;
const BUNDLE_TYPES = new Set(["hsw", "hsj", "hsl", "hsb"]);
const SCHEME_RE = /^[a-z][a-z0-9+.-]*:\/\//i;

const realms = new Map();

function jwtClaims(req) {
  if (typeof req !== "string") throw new Error("challenge JWT is not a string");
  const parts = req.split(".");
  if (parts.length !== 3) throw new Error("challenge JWT must have three parts");
  let claims;
  try {
    claims = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  } catch (err) {
    throw new Error("challenge JWT payload is not JSON: " + err.message);
  }
  if (!claims || typeof claims !== "object" || Array.isArray(claims)) {
    throw new Error("challenge JWT payload must be an object");
  }
  return claims;
}

function bundleEntry(claims) {
  const asset = claims.l;
  let type = typeof claims.n === "string" ? claims.n.toLowerCase() : "";
  if (!BUNDLE_TYPES.has(type)) type = /hsj/i.test(String(asset)) ? "hsj" : "hsw";
  return { asset, type };
}

function bundleUrl(asset, type) {
  if (typeof asset !== "string" || !asset) throw new Error("bundle path is missing from the JWT");
  let url;
  if (SCHEME_RE.test(asset)) {
    url = new URL(asset);
  } else {
    url = new URL(asset.startsWith("/") ? asset : "/" + asset, "https://newassets.hcaptcha.com");
  }
  if (url.protocol !== "https:") throw new Error("bundle URL must be https");
  if (url.username || url.password) throw new Error("bundle URL must not carry credentials");
  if (url.port && url.port !== "443") throw new Error("untrusted bundle port: " + url.port);
  const host = url.hostname.toLowerCase();
  if (host !== "hcaptcha.com" && !host.endsWith(".hcaptcha.com")) {
    throw new Error("untrusted bundle host: " + host);
  }
  let pathname = url.pathname.replace(/\/+$/, "");
  if (!pathname.endsWith("/" + type + ".js")) pathname += "/" + type + ".js";
  url.pathname = pathname;
  return url.toString();
}

async function loadBundle(url, userAgent) {
  const file = path.join(CACHE_DIR, nodeCrypto.createHash("sha256").update(url).digest("hex") + ".js");
  try {
    const stat = await fs.promises.stat(file);
    if (stat.size > 0 && Date.now() - stat.mtimeMs < CACHE_MAX_AGE_MS) {
      return await fs.promises.readFile(file, "utf8");
    }
  } catch (_) {
    // cold cache
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      headers: { "User-Agent": userAgent, Accept: "*/*", Referer: "https://newassets.hcaptcha.com/" },
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("bundle fetch failed: HTTP " + response.status);
    const code = await response.text();
    if (!code.trim()) throw new Error("bundle is empty");
    if (Buffer.byteLength(code, "utf8") > MAX_BUNDLE_BYTES) throw new Error("bundle is too large");
    try {
      await fs.promises.mkdir(CACHE_DIR, { recursive: true });
      await fs.promises.writeFile(file, code, { encoding: "utf8", mode: 0o600 });
    } catch (_) {
      // cache is best-effort
    }
    return code;
  } finally {
    clearTimeout(timer);
  }
}

function forgetOldestRealm() {
  const oldest = realms.keys().next();
  if (oldest.done) return;
  const entry = realms.get(oldest.value);
  realms.delete(oldest.value);
  try { entry.realm.dom.window.close(); } catch (_) { /* noop */ }
}

async function getEntry(claims, config) {
  const { asset, type } = bundleEntry(claims);
  const url = bundleUrl(asset, type);
  const key = [type, url, config.userAgent, config.screenWidth, config.screenHeight].join("|");
  const cached = realms.get(key);
  if (cached) return cached;

  const code = await loadBundle(url, config.userAgent);
  const realm = createRealm(config);
  await whenReady(realm.win);
  vm.runInContext(code, realm.ctx, { filename: url, timeout: RUN_TIMEOUT_MS });
  const fn = realm.win[type];
  if (typeof fn !== "function") throw new Error("bundle export not found: " + type);
  if (realms.size >= MAX_REALMS) forgetOldestRealm();
  const entry = { realm, fn };
  realms.set(key, entry);
  return entry;
}

async function solveProof({ req, host, href, vmData, config }) {
  const claims = jwtClaims(req);
  const { fn } = await getEntry(claims, config);
  const proof = await fn(req, {
    href: href || "https://" + host + "/",
    ardata: null,
    vm_data: vmData === undefined ? null : vmData,
    uj_data: null,
    errors: [],
    messages: [],
  });
  if (typeof proof !== "string" || proof.length === 0) {
    throw new Error("bundle returned no n-token");
  }
  return proof;
}

async function crypt(mode, req, bytes, config) {
  const claims = jwtClaims(req);
  const { fn } = await getEntry(claims, config);
  const out = await fn(mode, new Uint8Array(bytes));
  if (out instanceof Uint8Array) return Buffer.from(out);
  if (ArrayBuffer.isView(out)) return Buffer.from(out.buffer, out.byteOffset, out.byteLength);
  if (Array.isArray(out)) return Buffer.from(out);
  throw new Error("bundle returned unexpected crypto output: " + Object.prototype.toString.call(out));
}

module.exports = { solveProof, crypt, jwtClaims, bundleEntry, bundleUrl };
