# captcha-worker-protocol

Protocol-lane worker. Pulls `TaskEnvelope`s from the `protocol` stream and runs CPU-bound verifier solvers — Friendly Captcha PoW, Altcha PoW, anything else that reduces to a hash/crypto workload the server ships as a challenge.

## Why a separate lane

These solves burn CPU cores, not a network call. Mixing them with the `http` lane starves HTTP-bound tasks of the event loop; isolating them on `protocol` lets you scale cores (`--concurrency` ~= `os.cpu_count() * 2`) without touching the HTTP pool.

## Shape

- `main.py` — CLI entry. Installs uvloop (winloop on Windows), configures logging + Prometheus, opens Redis, builds a minimal `SolverContext` (no HTTP client, no browser, no model registry), discovers solvers via the `captcha.solvers` entry-point group, filters to those that route to the `protocol` lane under `captcha_queue.dispatch.pick_lane`, spawns N consumers.
- `worker.py` — `Worker`. One XREADGROUP loop. Per-task: resolve solver by `task.type`, call `solve_http` under a semaphore with a timeout from `task.deadline_ts`, write state + ack / fail.

## SolverContext subset

- `sensors`: shared `SensorRegistry` from `captcha_sensors.global_registry()`. Populated by any imported sensor-builder module.
- `config`: `captcha_common.Settings`.
- `logger`: shared structlog logger.
- `extra`: `{"redis": Redis}`. No HTTP factory, no proxy pool, no browser pool, no model registry — protocol solvers are pure CPU and self-contained.

## Run

```
uv run captcha-worker-protocol --consumers 2 --concurrency 32
```

PoW is CPU-bound; more than `--concurrency` in-flight doesn't finish faster, it just contends on the GIL. Scale by process count + core pinning.

## Hot path

PoW solvers do their heavy loop in `asyncio.to_thread` (or a shared `ProcessPoolExecutor` if a given challenge has no pure-Python fast path). That choice lives inside the solver, not the worker — the worker just awaits `solve_http`.
