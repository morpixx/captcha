# captcha-solver

HTTP-first captcha solver service. 2captcha/capsolver compatible API. Monorepo, Python 3.12, uv workspace.

## Scope

Backend only. There is no customer-facing frontend and none is planned for v0.1 —
clients talk to the gateway over the 2captcha HTTP protocol.
`services/admin` is a read-only internal dashboard (jinja2 + htmx, rendered on the
server); the service runs fine without it. Treat it as optional tooling, not a product surface.

## Current status

Honest inventory of what is actually in this tree. Nothing below is aspirational.

| Component | State | Notes |
|---|---|---|
| `packages/common` | done | `Task`/`TaskEnvelope`/`SolverResult`, 62 `CaptchaType` values, 2captcha schemas, error hierarchy, `Settings` |
| `packages/http` | done | curl_cffi session-per-task, JA3/JA4 profile table, retry policy, proxy normalization, 403/429/451 → `ChallengeExpiredError` |
| `packages/js_runtime` | done | py-mini-racer in a thread, script cache, Node sidecar client (sidecar itself is a separate image) |
| `packages/queue` | done | Redis Streams producer/consumer, XAUTOCLAIM stale reclaim, retry ceiling, DLQ, lane dispatch |
| `packages/db` | done | SQLAlchemy 2.0 async models, atomic charge/refund, Alembic + `0001_init` |
| `packages/crypto` | done | AES-CBC/GCM, RSA-OAEP/PKCS1v15, XOR, HMAC, raw deflate. No PoW module yet |
| `packages/observability` | done | structlog + correlation id, Prometheus registry, OTel, Sentry |
| `packages/proxy` | done | in-memory pool, sticky sessions, cooldown/ban marking, provider URL builders |
| `packages/behavior` | done | bezier mouse paths, humanized timing, mouse event payloads |
| `packages/models` | done | S3/MinIO weights registry, sha256 disk cache, canary slices, hot-reload watcher |
| `packages/fingerprint` | partial | file store + 6 handcrafted profiles. Bundles are structurally correct **scaffold data**, not live captures. S3 backend missing |
| `packages/browser` | done (unused) | Playwright/camoufox/patchright pool, context rotation, stealth helpers. No worker drives it yet |
| `packages/sensors` | interface only | 14 engine builders, all raise `SensorError("... not yet implemented")`. 56 `# FILL:` markers |
| `solvers/` | **empty** | only `_base` (ABC, entry-point registry, fixture harness). Zero solver plugins |
| `services/gateway` | done | createTask / getTaskResult / getBalance, healthz, readyz, /admin/stats, /admin/solvers, /metrics |
| `services/worker-http` | done | full dispatch loop, concurrency limiter, per-task context, timeout, retry/DLQ routing |
| `services/worker-cv` | done | same loop for the cv lane + model registry in context |
| `services/canary` | done | fixture-driven regression runner, alert webhook, `--once` mode |
| `services/admin` | done | per-type stats, solver list, task tail, proxy roster (optional) |
| `services/worker-protocol` | empty | directory only |
| `services/worker-browser` | empty | directory only |
| `tests/`, `docs/`, `runbooks/`, `ml/`, `infra/` | absent | referenced by docs and CI, do not exist |

**What works end-to-end right now:** `createTask` → auth → charge → Postgres row → XADD onto
a lane stream → worker picks it up → no solver is registered → `ERR_NO_SOLVER` → `getTaskResult`
returns `failed`. That is the whole path. Nothing in this tree produces a token, text, offset,
angle or tile index yet.

## Known breakage

- `uv sync --all-packages` fails: the root `pyproject.toml` declares 11 solver workspace members
  (and `services/worker-protocol`, `services/worker-browser`, `services/pipeline`) that have no
  code. Trim `[tool.uv.workspace].members` to the packages that exist until solvers land.
- `make dev` fails: `docker-compose.yml` builds from `infra/docker/*.Dockerfile`; the only
  Dockerfile in the repo is `packages/js_runtime/sidecar-node/Dockerfile`.
- `make test` exits 5: there are no test files.
- `make canary` needs `tests/canary/fixtures/*.json`; the directory does not exist, so the suite
  loads zero fixtures.
- Workers write task state to Redis only. `TaskRepo.update_status`/`update_result` are never
  called, so Postgres rows stay `pending` and the admin dashboard shows nothing useful.
- `SuccessRateTracker.should_fallback_to_browser` exists but is never called — the HTTP → browser
  demotion described in `plan.md` is not wired. `Consumer.fail` re-enqueues onto the same lane.
- No account seeding and no API to create accounts; insert one by hand to test `createTask`.

## Architecture

See `structure.md`, `plan.md`, `implementation-order.md`.

- `services/` — thin runnable processes (gateway, workers, canary, admin).
- `solvers/` — per-type plugins registered via `captcha.solvers` entry points. HTTP-first; browser
  only in `solvers/<type>/browser.py` when needed.
- `packages/` — shared libs: `http` (curl_cffi + JA3), `sensors` (per-engine payload builders),
  `js_runtime`, `queue`, `db`, `models`, `fingerprint`, `behavior`, `crypto`, `browser`.
- `runbooks/` — one per captcha type, to be added alongside each solver. CI is meant to enforce
  update-together; that CI does not exist yet either.

## Dev loop

```bash
uv sync                          # currently: installs nothing useful until the workspace members are fixed
cp .env.example .env             # then insert an account row by hand
make lint fmt                    # ruff (works today)
make typecheck                   # mypy
make test                        # no tests yet
make dev                         # blocked: Dockerfiles missing
make canary                      # blocked: no fixtures
```

Run a single service straight from the venv, no docker:

```bash
python -m captcha_gateway.main --port 8080
python -m captcha_worker_http --consumers 8 --concurrency 512
python -m captcha_worker_cv --concurrency 8
python -m captcha_canary.main --once
python -m captcha_admin.main            # optional dashboard
```

Requires Redis (`REDIS_URL`) and Postgres (`POSTGRES_URL`) reachable; `S3_*` only matters for the
model registry and worker-cv.

## Protocol

2captcha-compatible: `POST /createTask`, `POST /getTaskResult`, `POST /getBalance`. Payload mode
only in v0.1 — the client sends collected challenge data, we return the answer. URL mode is a later
tier. Business errors always come back as HTTP 200 with `errorId != 0`, because that is what the
client SDKs parse.

### Solution shapes

`getTaskResult.solution` is projected per captcha type by
[`solution.py`](services/gateway/src/captcha_gateway/solution.py) — a client looks for one specific
key, and the generic field name (`token`) is not it for half the types.

| family | solution keys |
|---|---|
| reCAPTCHA v2 / v3 (+enterprise, invisible, callback) | `gRecaptchaResponse`, `userAgent` |
| hCaptcha (+enterprise) | `gRecaptchaResponse`, `respKey`, `userAgent` |
| Turnstile, FunCaptcha, MTCaptcha, Prosopo, Yidun, Binance, VK, Yandex, CaptchaFox, Cutcaptcha, KeyCaptcha, Capy, Lemin, atb, Twitch, BotGuard, BotDeflector, CyberSiARA | `token`, `userAgent` |
| GeeTest v3 | `challenge`, `validate`, `seccode` (`validate + "|jordan"` when the solver omits it) |
| GeeTest v4 | `captcha_id`, `lot_number`, `pass_token`, `gen_time`, `captcha_output` |
| Cloudflare Challenge | `cf_clearance`, `cookies`, `token`, `userAgent` |
| DataDome, Akamai, Kasada, PerimeterX, TSPD, Temu | `cookies` + `domains: {host: {cookies}}`, `userAgent` |
| Imperva | above plus `utmvc` |
| Friendly, Altcha, Hunt, Castle, Basilisk, Alibaba, Tencent TenDI | nested `data` (+ `headers` where the engine needs a UA) |
| AWS WAF | `captcha_voucher` + `existing_token`, or `cookies["aws-waf-token"]` |
| text / math / image-to-text / audio | `text` |

Abstract geometry types have no single 2captcha shape — every engine invents its own — so those use
our own stable keys: `objects` (grid), `coordinates` (click / draw / drag / Shein), `bbox`,
`angle`, `offset`.

`SolverResult.extra` always wins: a solver that knows its engine can inject vendor-exact fields and
they pass through untouched. Anything not projected falls back to the generic fields so a newly
added `CaptchaType` keeps working before its projection is written.

## Roadmap

`implementation-order.md` ranks all 62 types into tiers 1-5 plus an interactive misc bucket, with
timelines. Tier 1 (PoW, OCR, audio, text LLM) is the first thing to build — none of it exists yet
despite what an earlier revision of this file claimed.
