# captcha-worker-http

Main HTTP worker. Pulls `TaskEnvelope`s from the `http` lane stream, dispatches each to the solver registered for its `CaptchaType`, and writes the result back to `TaskStateStore`. Shoots for 1000+ concurrent inflight tasks on a single process; no browser.

## Shape

- `main.py` — CLI entrypoint. Installs uvloop (winloop on Windows), configures logging, wires redis + pg + shared `SolverContext`, discovers solvers via `captcha.solvers` entry points, spawns N consumer tasks and waits on SIGTERM.
- `worker.py` — `Worker`. The per-consumer loop: XREADGROUP a batch, `asyncio.create_task` per message, resolve solver by `task.type` via `SolverRegistry.pick`, call `solve_http` with a timeout derived from `task.deadline_ts`, write `set_ready`/`set_failed`, XACK. On `CaptchaError` subclasses we route by `.code`; after `max_retries` the envelope goes to the lane's dead-letter via `Consumer.fail`.
- `stats.py` — `SuccessRateTracker`. Rolling 1h per-type success rate backed by a redis sorted set (`score=ts_ms`, `member=rand:ok|rand:err`). `should_fallback_to_browser(type)` votes the router away from HTTP when the rate falls below the threshold.
- `context.py` — `build_context`. One shared `captcha_http.Client` is never used across tasks (session-per-task invariant); instead the context carries a `make_http` factory the solver can call. The proxy pool, js runtime, fingerprint store, sensor registry, model registry are all shared.
- `concurrency.py` — `ConcurrencyLimiter`. A `asyncio.Semaphore` wrapper exposing `in_flight`/`capacity` for the metrics gauge.

## Run

```
uv run captcha-worker-http --consumers 8 --concurrency 512
```

`WORKER_HTTP_CONCURRENCY` from `captcha_common.Settings` is the default global in-flight cap; `--consumers` is how many parallel XREADGROUP loops share it. More consumers only help when the semaphore isn't the bottleneck.

## Backpressure

The semaphore gates between read and solve. When saturated, the XREADGROUP loop blocks on the acquire; new messages pile up on redis instead of in memory, which is what we want — redis has fsync, this process does not.
