# captcha-admin

Minimal FastAPI + jinja2 + htmx 2.0 dashboard for the captcha service.
Read-only — never mutates DB rows.

## Pages

| route | content |
|---|---|
| `/` | per-type solved/failed counts, p50/p95 latency, last 24h/7d |
| `/solvers` | one row per registered solver: type coverage, last run, success rate |
| `/solvers/<name>` | per-solver run history, error codes, latency histogram |
| `/tasks` | last N tasks, filterable by type/status |
| `/proxies` | proxy pool roster + pass/fail per host (stub until proxy pkg exports stats) |
| `/healthz` | `{"ok": true}` for probes |

## Run

```
python -m captcha_admin.main
# serves on 0.0.0.0:8081 by default
```

## Environment

| var | default |
|---|---|
| `CAPTCHA_ADMIN_HOST` | `0.0.0.0` |
| `CAPTCHA_ADMIN_PORT` | `8081` |
| `POSTGRES_URL` | (inherited from common.config) |
| `REDIS_URL` | (inherited from common.config) |
