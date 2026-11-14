# captcha-worker-browser

Browser-lane worker. Pulls `TaskEnvelope`s from the `browser` stream and drives a real browser — camoufox (Firefox, max stealth) → patchright → vanilla playwright, picked at pool launch. Last-resort transport for URL-mode captchas and antibot chains that insist on a real client (CF Challenge pages that pin session cookies to TLS handshake state, DataDome device-check redirects, FunCaptcha URL-mode).

## Why a separate lane

Browsers are 1000× heavier than a `curl_cffi` call. Mixing them with HTTP-lane workers starves memory and spins up more browsers than we can afford. Keep them to 1–3 nodes with 15–25 sessions each, which `settings.worker_browser_sessions` caps.

## Shape

- `main.py` — CLI entry. Installs uvloop (winloop on Windows), configures logging + Prometheus, opens Redis, launches a `captcha_browser.Pool` with `size = settings.worker_browser_sessions`, builds `SolverContext` with the pool + a proxy pool + a fingerprint store, discovers solvers via the `captcha.solvers` entry-point group, filters to those that route to the `browser` lane under `captcha_queue.dispatch.pick_lane`, spawns N consumers that share the pool.
- `worker.py` — `Worker`. One XREADGROUP loop. Per-task: resolve solver, call `solve_browser` (never `solve_http` on this lane) under a semaphore gated by the pool's capacity, timeout from `task.deadline_ts`, state transitions, ack / fail. The solver itself acquires a `Session` from `ctx.browser` — the worker does not pre-bind one, because solvers may want to pick a profile per-task.

## SolverContext subset

- `browser`: `captcha_browser.Pool` — already launched. Solvers do `async with ctx.browser.acquire() as sess: ...`.
- `proxy_pool`: `captcha_proxy.Pool`, if configured. Used to pick a proxy before acquiring a browser session.
- `fingerprint_store`: `captcha_fingerprint.Store`, TLS / canvas / webgl profiles.
- `sensors`: `SensorRegistry` — browser-mode solvers may still need sensor payloads for interactive subchallenges.
- `config`: `captcha_common.Settings`.
- `logger`: shared structlog logger.
- `extra`: `{"redis": Redis}`. No HTTP client here — if a solver needs plain HTTP it grabs one off the Session's `context.request` or uses `captcha_http` directly.

## Run

```
uv run captcha-worker-browser --consumers 1 --concurrency 20
```

`WORKER_BROWSER_SESSIONS` caps the Pool. `--concurrency` caps in-flight tasks and should be `<=` the pool size; otherwise tasks will block waiting on `Pool.acquire`, which is fine but wastes CPU.

## Shutdown

SIGINT/SIGTERM → drain in-flight → `Pool.close_all()` → Redis close. A hung page can hold a session — the pool will close the context forcibly on shutdown.
