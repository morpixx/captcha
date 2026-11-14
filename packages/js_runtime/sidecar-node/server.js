// Node sidecar for captcha-js-runtime.
// Each /exec builds a fresh vm.Context seeded with request globals, runs the script
// with a per-request timeout, and returns the result as JSON.
//
// node:vm is not a true sandbox — the isolate boundary is cooperative. We bind only
// a hardcoded set of safe shims (crypto, TextEncoder, URL, atob/btoa, Math, Date,
// JSON, console-no-op) and strip Node-specific globals. Trusted-ish scripts only.

import Fastify from "fastify";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import { TextEncoder, TextDecoder } from "node:util";
import { URL as NodeURL, URLSearchParams } from "node:url";

const PORT = parseInt(process.env.SIDECAR_PORT || "7474", 10);
const HOST = process.env.SIDECAR_HOST || "0.0.0.0";
const MAX_SCRIPT_BYTES = 2 * 1024 * 1024;

const app = Fastify({
    logger: {
        level: process.env.LOG_LEVEL || "info",
    },
    bodyLimit: 4 * 1024 * 1024,
});

function buildSandbox(globals) {
    const noopConsole = {
        log: () => {},
        warn: () => {},
        error: () => {},
        info: () => {},
        debug: () => {},
    };
    const atobFn = typeof atob === "function" ? atob : (s) => Buffer.from(s, "base64").toString("binary");
    const btoaFn = typeof btoa === "function" ? btoa : (s) => Buffer.from(s, "binary").toString("base64");

    const sandbox = {
        // Minimum web platform surface a captcha interrogator reaches for.
        crypto: webcrypto,
        TextEncoder,
        TextDecoder,
        URL: NodeURL,
        URLSearchParams,
        atob: atobFn,
        btoa: btoaFn,
        Math,
        Date,
        JSON,
        Error,
        TypeError,
        RangeError,
        ReferenceError,
        Array,
        Object,
        String,
        Number,
        Boolean,
        RegExp,
        Map,
        Set,
        WeakMap,
        WeakSet,
        Symbol,
        Promise,
        Uint8Array,
        Uint16Array,
        Uint32Array,
        Int8Array,
        Int16Array,
        Int32Array,
        Float32Array,
        Float64Array,
        ArrayBuffer,
        DataView,
        parseInt,
        parseFloat,
        isNaN,
        isFinite,
        console: noopConsole,
    };

    if (globals && typeof globals === "object") {
        for (const [k, v] of Object.entries(globals)) {
            // Shadow reserved keys harmlessly — a challenge script that passes `crypto`
            // in context_globals overrides our shim deliberately.
            sandbox[k] = v;
        }
    }
    return sandbox;
}

app.get("/health", async () => ({ ok: true }));

app.post("/exec", async (request, reply) => {
    const { script, globals, timeout } = request.body || {};

    if (typeof script !== "string") {
        reply.code(400);
        return { ok: false, error: { kind: "bad_request", message: "script must be string" } };
    }
    if (script.length > MAX_SCRIPT_BYTES) {
        reply.code(413);
        return { ok: false, error: { kind: "too_large", message: "script exceeds size limit" } };
    }
    const timeoutMs = Number.isInteger(timeout) && timeout > 0 ? Math.min(timeout, 30000) : 5000;

    const sandbox = buildSandbox(globals);
    const context = vm.createContext(sandbox, {
        name: "captcha-exec",
        codeGeneration: { strings: false, wasm: false },
    });

    let compiled;
    try {
        compiled = new vm.Script(script, { filename: "exec.js" });
    } catch (e) {
        return {
            ok: false,
            error: { kind: "compile", message: String(e && e.message || e), stack: e && e.stack },
        };
    }

    try {
        const result = compiled.runInContext(context, {
            timeout: timeoutMs,
            breakOnSigint: true,
        });
        // Promise-returning scripts: wait on them within the same budget. node:vm's
        // timeout does NOT apply across await points, so we cap with Promise.race.
        let value = result;
        if (value && typeof value.then === "function") {
            value = await Promise.race([
                value,
                new Promise((_, rej) =>
                    setTimeout(() => rej(new Error("script async timeout")), timeoutMs),
                ),
            ]);
        }
        // Round-trip through JSON to enforce serializable-only output.
        const safe = JSON.parse(JSON.stringify(value === undefined ? null : value));
        return { ok: true, result: safe };
    } catch (e) {
        const msg = String(e && e.message || e);
        const kind = /time/i.test(msg) ? "timeout" : "runtime";
        return {
            ok: false,
            error: { kind, message: msg, stack: e && e.stack },
        };
    }
});

app.listen({ host: HOST, port: PORT })
    .then(() => {
        app.log.info({ port: PORT, host: HOST }, "captcha-js-sidecar listening");
    })
    .catch((err) => {
        app.log.error(err);
        process.exit(1);
    });
