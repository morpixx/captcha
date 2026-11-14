# captcha-js-runtime

V8 sandbox for running obfuscated challenge JS (Cloudflare interrogator, FunCaptcha BDA
builder, DataDome parts, hCaptcha enterprise payload bits).

## Why two backends

`py-mini-racer` is the fast path — in-process V8, zero IPC, thread-executor wrapped so it
stays async-friendly. It handles 90% of what captcha challenge scripts throw at us.

A Node sidecar is the escape hatch for scripts mini-racer can't run (ES2020+ features it
lags on, scripts leaning on `TextDecoder`/`crypto.subtle`/WebCrypto in ways mini-racer's
shims don't cover, scripts over mini-racer's practical memory ceiling). Spin it up with
docker-compose, point `JS_SIDECAR_URL` at it, and `pick_runtime` will route long scripts
to the sidecar.

## Usage

```python
from captcha_js_runtime import LocalRuntime, ScriptCache

rt = LocalRuntime(timeout_ms=5000)
result = await rt.exec_script("f(x)", context_globals={"x": 42})
```

Pick automatically:

```python
from captcha_js_runtime import pick_runtime

rt = pick_runtime(sidecar_url=settings.js_sidecar_url)
```

## Sidecar

`sidecar-node/` is a Fastify server on `:7474` that evaluates scripts in `node:vm`
contexts with a per-request 5s timeout. Build and run:

```
cd sidecar-node && docker build -t captcha-js-sidecar . && docker run -p 7474:7474 captcha-js-sidecar
```
