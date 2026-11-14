# captcha-gateway

2captcha-compatible HTTP front end. Accepts `createTask`, persists it, enqueues
to the right lane stream, then answers `getTaskResult` out of the Redis state
hash (with Postgres as the long-tail fallback). No solving happens here — the
gateway is deliberately thin.

## Run

```
uv run python -m captcha_gateway.main --host 0.0.0.0 --port 8080
```

Env vars via `captcha_common.Settings` (`CAPTCHA_GATEWAY_HOST`,
`CAPTCHA_GATEWAY_PORT`, `REDIS_URL`, `POSTGRES_URL`, ...). On Linux, uvicorn
uses the `uvloop` loop; on Windows it falls back to the default asyncio loop.

## Routes

- `POST /createTask`      — validate, charge, enqueue, return `taskId`
- `POST /getTaskResult`   — Redis-first lookup; DB fallback for aged tasks
- `POST /getBalance`      — Postgres read on the account row
- `GET  /healthz`         — liveness
- `GET  /readyz`          — Redis + Postgres ping
- `GET  /admin/stats`     — task counts by status
- `GET  /admin/solvers`   — registered solvers via entry_points
- `GET  /metrics`         — Prometheus
