"use strict";

// hCaptcha bundle worker: newline-delimited JSON on stdin/stdout.
//
//   {"mode":"solve","req":"<jwt>","host":"example.com","href":"https://…","config":{…},"vm_data":"…"}
//   {"mode":"encrypt"|"decrypt","req":"<jwt>","data":"<base64>","config":{…}}
//
// Answers:
//   {"status":"ok","kind":"string"|"bytes","data":"<base64>","len":N}
//   {"status":"error","error":"…"}
//
// One request at a time, in arrival order; the Python runner owns the deadline.
// stderr is reserved for diagnostics (bundle warnings, stack traces).

const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { solveProof, crypt } = require("./bundle");

const DEFAULTS = Object.freeze({
  userAgent:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    + "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  screenWidth: 1920,
  screenHeight: 1080,
  hardwareConcurrency: 16,
  maxTouchPoints: 0,
  platform: "Win32",
});

function normalizeConfig(raw) {
  const config = Object.assign({}, DEFAULTS, raw && typeof raw === "object" ? raw : {});
  for (const key of ["screenWidth", "screenHeight", "hardwareConcurrency", "maxTouchPoints"]) {
    if (!Number.isInteger(config[key]) || config[key] < 0) config[key] = DEFAULTS[key];
  }
  if (typeof config.userAgent !== "string" || !config.userAgent) config.userAgent = DEFAULTS.userAgent;
  if (typeof config.platform !== "string" || !config.platform) config.platform = DEFAULTS.platform;
  return config;
}

async function handle(task) {
  if (!task || typeof task !== "object") throw new Error("request must be a JSON object");
  const config = normalizeConfig(task.config);
  if (task.mode === "solve") {
    if (typeof task.host !== "string" || !task.host) throw new Error("host is required for solve");
    const proof = await solveProof({
      req: task.req,
      host: task.host,
      href: typeof task.href === "string" ? task.href : undefined,
      vmData: task.vm_data,
      config,
    });
    return { kind: "string", bytes: Buffer.from(proof, "latin1") };
  }
  if (task.mode === "encrypt" || task.mode === "decrypt") {
    if (typeof task.data !== "string" || !task.data) throw new Error("data (base64) is required");
    const input = Buffer.from(task.data, "base64");
    const output = await crypt(task.mode === "encrypt" ? 1 : 0, task.req, input, config);
    return { kind: "bytes", bytes: output };
  }
  throw new Error("unknown mode: " + String(task.mode));
}

async function handleLine(line) {
  if (!line.trim()) return;
  let task;
  try {
    task = JSON.parse(line);
  } catch (_) {
    process.stdout.write(JSON.stringify({ status: "error", error: "malformed JSON request" }) + "\n");
    return;
  }
  try {
    const result = await handle(task);
    process.stdout.write(
      JSON.stringify({
        status: "ok",
        kind: result.kind,
        data: result.bytes.toString("base64"),
        len: result.bytes.length,
      }) + "\n",
    );
  } catch (err) {
    const message = String(err && err.message ? err.message : err);
    if (process.env.HCAPTCHA_JS_DEBUG && err && err.stack) process.stderr.write(err.stack + "\n");
    process.stdout.write(JSON.stringify({ status: "error", error: message.slice(0, 2000) }) + "\n");
  }
}

async function main() {
  for (const dir of [path.join(__dirname, ".cache")]) {
    try {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    } catch (_) {
      // cache is best-effort
    }
  }
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    await handleLine(line);
  }
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(String(err && err.stack ? err.stack : err) + "\n");
    process.exit(1);
  });
}

module.exports = { handle, normalizeConfig };
