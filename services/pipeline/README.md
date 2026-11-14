# captcha-pipeline

Three background jobs that keep the solver fleet learning:

| job | cadence | what it does |
|---|---|---|
| **sampler** | hourly | reads recent `Task` rows per type, dumps the raw `payload + result` as JSON into S3 under `s3://<bucket>/labelling/<type>/<yyyymmdd>/<task_id>.json` for human labelling |
| **drift** | 15 min | computes per-type success rate over the last N hours, compares to the prior week baseline, fires a webhook when a type regresses |
| **retrain** | on drift flag or manual | writes a `retrain_requested` marker JSON to `s3://<bucket>/retrain_requests/` and POSTs the retrain webhook |

Read-only against Postgres. Writes only to S3 and the webhook.

## Run

```
# one-shot (CI / manual)
python -m captcha_pipeline.main --once sample
python -m captcha_pipeline.main --once drift
python -m captcha_pipeline.main --once retrain --reason manual

# daemon (prod) — runs all three on their configured intervals
python -m captcha_pipeline.main
```

## Environment

| var | meaning | default |
|---|---|---|
| `POSTGRES_URL` | async DSN | inherited from `captcha_common` |
| `S3_ENDPOINT` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_BUCKET` / `S3_REGION` | S3 target (MinIO-compatible) | inherited |
| `PIPELINE_S3_PREFIX` | key prefix for samples | `labelling` |
| `PIPELINE_RETRAIN_PREFIX` | key prefix for retrain markers | `retrain_requests` |
| `PIPELINE_SAMPLE_INTERVAL_SEC` | sampler cadence | `3600` |
| `PIPELINE_SAMPLE_WINDOW_HOURS` | lookback for sampling | `24` |
| `PIPELINE_SAMPLE_PER_TYPE` | samples per type per run | `500` |
| `PIPELINE_SAMPLE_TYPES` | CSV filter (empty = all types seen in window) | (unset) |
| `PIPELINE_SAMPLE_STATUSES` | CSV status filter | `ready,failed` |
| `PIPELINE_DRIFT_INTERVAL_SEC` | drift cadence | `900` |
| `PIPELINE_DRIFT_WINDOW_HOURS` | recent window | `6` |
| `PIPELINE_DRIFT_BASELINE_DAYS` | baseline window after skipping recent | `7` |
| `PIPELINE_DRIFT_MIN_SAMPLES` | ignore types below this count in recent window | `50` |
| `PIPELINE_DRIFT_ABS_THRESHOLD` | flag when recent success_rate < this | `0.75` |
| `PIPELINE_DRIFT_REL_THRESHOLD` | flag when recent/baseline < this | `0.85` |
| `PIPELINE_DRIFT_WEBHOOK` | JSON POST target on drift | (unset) |
| `PIPELINE_RETRAIN_WEBHOOK` | JSON POST target on retrain trigger | (unset) |
| `PIPELINE_RETRAIN_ON_DRIFT` | auto-trigger retrain for each flagged type | `false` |

## S3 layout

```
<bucket>/
  labelling/
    turnstile/20261114/<task_id>.json
    recaptcha_v2/20261114/<task_id>.json
    ...
  retrain_requests/
    202611141530_turnstile.json
    202611141530_recaptcha_v2.json
```

Each labelling blob is self-contained: `payload`, `result`, status, latency,
timestamps, worker id. Enough to replay the solve offline.
