# Load tests: createTask at 1k/s

Measures the gateway ingress, not a captcha vendor. A fake solver drains the
lane streams and writes results back to Redis immediately, so the numbers are
the gateway + queue + (optionally) Postgres cost of one `POST /createTask`.

```
k6 / locust ──POST /createTask──▶ gateway ──XADD──▶ captcha:stream:<lane>
                                                          │
                                        tools/load/fake_solver.py
                                                          │
                                               HSET captcha:task:<id>
                                                          │
k6 / locust ──GET /getTaskResult───────────────────────────┘
```

Nothing here contacts a live captcha site, a proxy, a model or a browser.

## Files

| file | purpose |
|---|---|
| `create_task.js` | k6, constant-arrival-rate, default 1000/s |
| `locustfile.py` | locust alternative for a long soak / machines without k6 |
| `fake_solver.py` | consumes every lane, answers `ready` in Redis (optional Postgres mirror) |

## Prerequisites

- docker compose (Redis + Postgres)
- k6 (`winget install k6`, `brew install k6`, or the apt repo)
- the repo's Python environment (`uv sync --extra dev`) for the local gateway,
  the account CLI and the fake solver

## 1. Infrastructure

Start only the datastores and the migration; do **not** start the real workers
— the fake solver has to be the only consumer for these tests.

```bash
docker compose up -d redis postgres migrate
```

## 2. Gateway with quotas disabled

The production defaults (60 creates/min, 100 active tasks) reject a 1k/s load in
under a second. Override them in the shell that starts the gateway; never put
load-test values into `.env`.

PowerShell:

```powershell
$env:PYTHONPATH = "$PWD/src"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:POSTGRES_URL = "postgresql+asyncpg://captcha:captcha@127.0.0.1:5432/captcha"
$env:RATE_LIMIT_CREATE_PER_MIN = "0"
$env:MAX_ACTIVE_TASKS_PER_ACCOUNT = "0"
$env:MAX_DAILY_TASKS = "0"
$env:MAX_DAILY_SPEND = "0"
uv run python -m captcha.services.gateway
```

bash:

```bash
export PYTHONPATH="$PWD/src"
export RATE_LIMIT_CREATE_PER_MIN=0 MAX_ACTIVE_TASKS_PER_ACCOUNT=0
export MAX_DAILY_TASKS=0 MAX_DAILY_SPEND=0
uv run python -m captcha.services.gateway
```

One process is enough for a smoke run. For a sustained 1k/s use the container
image with more uvicorn workers (`CAPTCHA_GATEWAY_WORKERS`) and watch CPU: the
gateway is the system under test.

## 3. Funded account

```bash
uv run python -m captcha.services.cli create-account --name load --balance 500
# prints: key <hex>  — export it
export CLIENT_KEY=<hex>
```

60 seconds at 1k/s is 60 000 tasks. At the catalog price of `text_captcha`
(0.0005) that is ~30 currency units; 500 leaves room for repeats.

## 4. Fake solver

```bash
export PYTHONPATH="$PWD/src"
python tools/load/fake_solver.py --redis redis://127.0.0.1:6379/0
```

It consumes `http`, `cv`, `protocol` and `browser` lanes with its own consumer
groups, so it can run next to the stack without stealing messages. `--db` also
mirrors each result into Postgres — use it only when storage is part of the test,
since 1k inserts/s makes Postgres the bottleneck.

## 5. Run k6

```bash
k6 run --env CLIENT_KEY="$CLIENT_KEY" \\
       --env BASE_URL=http://127.0.0.1:8080 \\
       --env RATE=1000 \\
       --env DURATION=60s \\
       tools/load/create_task.js
```

or through the Makefile:

```bash
make load KEY="$CLIENT_KEY" URL=http://127.0.0.1:8080 RATE=1000 DURATION=60s
```

Useful knobs: `TYPE=text_captcha,image_to_text` (comma-separated mix),
`PRE_VUS`/`MAX_VUS` when k6 reports `dropped_iterations`.

Thresholds in `create_task.js`:

| metric | threshold | meaning |
|---|---|---|
| `checks` | >99.9% | `errorId == 0` and a task id returned |
| `http_req_failed` | <0.1% | transport/5xx failures only |
| `create_task_latency_ms` p95 | <300 ms | ingress latency |
| `create_task_latency_ms` p99 | <750 ms | tail |
| `dropped_iterations` | 0 | the generator kept up with 1k/s |

A rejected request is logged every 100 failures with its `errorId`. The usual
ones: `ERROR_NO_SLOT_AVAILABLE` (a quota was not disabled), `ERROR_ZERO_BALANCE`
(account empty), `ERROR_TASK_NOT_SUPPORTED` (type not registered in this
deployment).

## 6. Locust alternative

```bash
export CLIENT_KEY=...
locust -f tools/load/locustfile.py --headless \\
       -u 1000 -r 200 --run-time 60s --host http://127.0.0.1:8080
```

Locust is VU-based: 1 000 users with `wait_time = 0` approximates 1k/s only if
the gateway latency stays around 1s per request. For an honest arrival-rate
target use k6; use locust for a long soak and per-request statistics.

## Cleanup

The run creates tens of thousands of rows and task hashes. On a throwaway stack:

```bash
docker compose down -v
```

On a shared dev stack, remove only the load-test keys:

```bash
redis-cli --scan --pattern 'captcha:task:*' | xargs -r redis-cli del
redis-cli --scan --pattern 'captcha:stream:*' | xargs -r redis-cli del
```

and truncate `tasks`/`transactions` if `--db` was used. Never point this at
production: the load test disables quotas and bills a real account.
