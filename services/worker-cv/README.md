# captcha-worker-cv

CV-lane worker. Pulls `TaskEnvelope`s from the `cv` stream and runs image/audio/geometry solves on GPU. Each solver is expected to be `solve_http`-callable — the "cv" label is a scheduling detail (keep GPU-bound work off the http pool), not a transport one.

## Shape

- `main.py` — CLI entry. Installs uvloop (winloop on Windows), configures logging + Prometheus, opens Redis, builds a `SolverContext` with `captcha_models.Registry` and `device = settings.worker_cv_device`, discovers solvers via the `captcha.solvers` entry-point group, filters to those that route to the `cv` lane under `captcha_queue.dispatch.pick_lane`, spawns N consumers.
- `worker.py` — `Worker`. One XREADGROUP loop. Per-task: resolve solver by `task.type`, call `solve_http` under a shared semaphore with a timeout derived from `task.deadline_ts`, write state + ack / fail. Uses `UnsolvableError` as terminal signal; everything else goes through `Consumer.fail` with retry / DLQ routing.

## SolverContext subset

- `models`: `captcha_models.Registry`, S3-backed with disk cache. Device from `settings.worker_cv_device` (`cpu` / `cuda` / `mps`). Solvers call `ctx.models.get(name)` on demand in `prepare()`.
- `config`: `captcha_common.Settings`.
- `logger`: shared structlog logger.
- `extra`: `{"redis": Redis, "device": str}`. CV solvers never open HTTP sessions or launch browsers.

## Run

```
uv run captcha-worker-cv --consumers 2 --concurrency 8
```

`WORKER_CV_DEVICE` picks the inference device. One worker process per GPU — with CUDA set `CUDA_VISIBLE_DEVICES` so each process sees exactly its card.

## Backpressure

The semaphore gates the GPU. On a single-GPU box more than `--concurrency` in-flight only wastes VRAM; the extra tasks pile up on Redis (where they're fsynced), not in memory.
