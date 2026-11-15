# infra

Deployment shape for the captcha service. The contract is
`docs/production-contract.md`; this file is the operator's half.

## Images

| file | roles | size |
|---|---|---|
| `docker/app.Dockerfile` | gateway, worker-http, worker-cv, worker-protocol, migrate, canary | ~600 MB (+torch for cv) |
| `docker/browser.Dockerfile` | worker-browser | ~2.5 GB (Camoufox/Firefox) |

Both images carry Node 20 and `node_modules` for
`src/captcha/adapters/hcaptcha_js` (`npm ci` against the committed
`package-lock.json`), because the hCaptcha HTTP solver spawns that bundle
worker as a subprocess.

The browser image installs `camoufox[geoip]` into the project venv *after* the
last `uv sync` — camoufox is not a pyproject extra, and `uv sync` prunes what
the project does not list. The browser build is fetched in its own layer, so
editing source does not re-download 2.5 GB.

## Compose

```bash
# production: Caddy on 80/443 is the only published port
docker compose up -d --build

# + browser lane (builds the big image) and the 6h canary suite
docker compose --profile browser --profile canary up -d --build

# local: gateway on 127.0.0.1:8080, Caddy and profiles off
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

`make dev` runs `docker-compose.yml` alone, which is the production shape: the
gateway has no host port and Caddy is up. Use the overlay for local work.

| service | published | healthcheck |
|---|---|---|
| caddy | 80, 443/tcp, 443/udp | none (depends on gateway health) |
| gateway | — (`expose: 8080`) | `GET /healthz` |
| redis / postgres / minio | 127.0.0.1 only | native |
| worker-* | — | `captcha-healthcheck --redis` |
| worker-browser | — | `captcha-healthcheck --redis --browser` |

`worker-browser` gets `shm_size: 2gb` (Firefox IPC), a `/tmp` volume (per-task
profiles are reaped on boot), and `deploy.resources.limits` from
`BROWSER_CPUS`/`BROWSER_MEMORY`.

The canary mounts `tests/canary/fixtures` read-only. That directory is owned by
the observability work and is not in this image; a missing directory is not an
error (the suite logs `canary.run.no_fixtures` and passes), so check the
container log once after the first canary deploy.

## Required env in production

`ADMIN_TOKEN` (empty disables `/admin/*`), `API_KEY_PEPPER` (key hashing at
rest), `CAPTCHA_DOMAIN` + `ACME_EMAIL` (Caddy). See `.env.example`.

## TLS ingress

`caddy/Caddyfile`: `{$CAPTCHA_DOMAIN}`, `tls {$ACME_EMAIL:internal}` (empty
email -> internal CA), 40 MB body cap (hopscotch is <= 32 MiB), HSTS and
nosniff headers, `X-Real-IP` forwarded. Access logging is off on purpose:
`/in.php?key=...` and `/res.php?...&key=...` carry the client key in the query
string, and the contract forbids logging keys or payloads.
