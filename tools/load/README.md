# Load tests

Two ways to put the gateway under load. Neither contacts a captcha vendor, a
proxy, a model or a browser: the point is the ingress, the queue and the state
store.

How to read the numbers the Python driver prints, and what each field means:
`docs/load-testing.md`. This file is the operator's map of the directory.

| path | what it is |
|---|---|
| `config.py` | frozen driver config, built from argv; the key is masked in the report |
| `run.py` | `python -m tools.load.run` — open-loop `createTask`/`getTaskResult` driver |
| `scenario.py` | the paced loop behind the driver (one sample = create + poll to terminal) |
| `stats.py`, `report.py` | percentiles, time buckets, text/JSON rendering, verdicts |
| `queue_probe.py` | `XLEN` + consumer-group lag sampled straight from Redis |
| `devstack.py` | `python -m tools.load.devstack` — real gateway + real worker loops, fake solver |
| `fake_solver.py` | in-process `Solver` that sleeps and answers `text='load-test'` |
| `stream_drainer.py` | external drainer: answers lane streams without starting a gateway |
| `create_task.js` | k6, constant-arrival-rate, default 1000/s |
| `locustfile.py` | locust variant for a long soak or a machine without k6 |

## A. Python driver (primary)

Everything below is production code except the solver call itself.

```bash
# terminal 1: stack on one box (redis :6399, pg :55432 — any throwaway pair)
PYTHONPATH=src python -m tools.load.devstack \
    --type text_captcha --workers 4 --concurrency 1024 \
    --redis-url redis://127.0.0.1:6399/1 \
    --postgres-url postgresql+asyncpg://captcha:captcha@127.0.0.1:55432/captcha --flush

# terminal 2: load + report
PYTHONPATH=src python -m tools.load.run \
    --url http://127.0.0.1:8080 --key <clientKey> --type text_captcha \
    --rate 1000 --duration 60 --redis-url redis://127.0.0.1:6399/1 \
    --json runs/soak-01.json --label soak-01 --strict
```

Through the Makefile:

```bash
make load-stack                       # devstack, waits for Ctrl+C
make load-run KEY=<clientKey> RATE=1000 DURATION=60
```

Prerequisites are outside the process: Redis and Postgres with migrations
applied (`python -m captcha.services.cli init-db`) and a funded account
(`python -m captcha.services.cli create-account --name load --balance 500`).
The stand disables quotas itself, otherwise a soak measures the limiter.

## B. External generator (k6 / locust)

Use this when the gateway is already running — a deployed compose, or the
`devstack` from section A — and you want an arrival-rate generator you do not
have to trust by the driver's own numbers.

```bash
make load KEY=<clientKey> URL=http://127.0.0.1:8080 RATE=1000 DURATION=60s
make load-locust KEY=<clientKey> USERS=1000 SPAWN=200 DURATION=60s
```

`make load` runs `create_task.js` (k6 constant-arrival-rate, thresholds below).
`make load-locust` runs the locust variant: VU-based, so 1000 users approximate
1000/s only while the gateway answers in about a second; for honest arrival
rates use k6.

Tasks must be answered by something. Two options:

- `python -m tools.load.devstack` already runs the real worker loops with the
  fake solver — point k6 at the gateway it served;
- on a stack whose workers you do not control, run the external drainer:

  ```bash
  PYTHONPATH=src python tools/load/stream_drainer.py \
      --redis redis://127.0.0.1:6379/0 --prefix captcha
  ```

  It consumes every lane with its own consumer groups, writes `processing` then
  `ready` into the task hash and acks. `--db` also mirrors results into Postgres;
  leave it off unless storage is part of the measurement.

k6 thresholds in `create_task.js`:

| metric | threshold | meaning |
|---|---|---|
| `checks` | >99.9% | `errorId == 0` and a task id returned |
| `http_req_failed` | <0.1% | transport/5xx failures only |
| `create_task_latency_ms` p95 | <300 ms | ingress latency |
| `create_task_latency_ms` p99 | <750 ms | tail |
| `dropped_iterations` | 0 | the generator kept up with 1000/s |

A rejected request is logged every 100 failures with its `errorId`. The usual
ones: `ERROR_NO_SLOT_AVAILABLE` (a quota was not disabled), `ERROR_ZERO_BALANCE`,
`ERROR_TASK_NOT_SUPPORTED` (the type is not registered in this deployment).

## Cleanup

A run creates tens of thousands of rows and task hashes. On a throwaway stack:

```bash
docker compose down -v
```

On a shared dev stack remove only the load-test keys:

```bash
redis-cli --scan --pattern 'captcha:task:*' | xargs -r redis-cli del
redis-cli --scan --pattern 'captcha:stream:*' | xargs -r redis-cli del
```

and truncate `tasks`/`transactions` if `--db` was used. Never point this at
production: the load test disables quotas and bills a real account.

## Tests

```bash
PYTHONPATH=src pytest tests/load -q
```

No Redis or Postgres needed: the driver runs against a mocked gateway and the
fake solver is exercised through the registry.
