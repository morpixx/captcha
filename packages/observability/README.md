# captcha-observability

One place for logging, metrics, tracing, and error reporting across the captcha-solver monorepo.

## usage

```python
from captcha_common import get_settings
from captcha_observability import (
    setup_logging,
    setup_metrics,
    setup_tracing,
    setup_sentry,
    metrics,
    build_metrics_app,
)

settings = get_settings()

setup_logging(level=settings.log_level, json=settings.log_json)
setup_metrics()
setup_tracing(service_name="gateway", endpoint=settings.otel_endpoint)
setup_sentry(dsn=settings.sentry_dsn, environment="prod")

# emit a metric
metrics.tasks_created_total.labels(type="recaptcha_v2", lane="http").inc()

# mount /metrics in starlette/fastapi
app.mount("/metrics", build_metrics_app())
```

## correlation ids

`setup_logging` installs a `correlation_id` contextvar processor. Bind it per-request:

```python
from captcha_observability.logging import correlation_id

token = correlation_id.set(request_id)
try:
    ...
finally:
    correlation_id.reset(token)
```

## metrics catalogue

| name | kind | labels |
| --- | --- | --- |
| `captcha_tasks_created_total` | counter | `type`, `lane` |
| `captcha_tasks_done_total` | counter | `type`, `status`, `solve_mode` |
| `captcha_task_latency_seconds` | histogram | `type`, `solve_mode` |
| `captcha_solver_errors_total` | counter | `type`, `error_code` |
| `captcha_proxy_bans_total` | counter | `provider` |
| `captcha_sensor_build_latency_seconds` | histogram | `engine` |
| `captcha_active_tasks` | gauge | `lane` |

All metrics live on a dedicated `CollectorRegistry` — nothing leaks into the default one, so re-import under tests is clean.
