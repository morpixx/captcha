# captcha-canary

Fixture-driven regression canary. Every interval it loads
`tests/canary/fixtures/*.json`, posts each to the gateway's `/createTask`,
polls `/getTaskResult` until terminal, and records pass/fail + latency.
When the rolling pass-rate drops below `CANARY_PASS_THRESHOLD`
it posts a JSON alert to `CANARY_ALERT_WEBHOOK`.

## Run

```
# one-shot (CI)
python -m captcha_canary.main --once

# daemon (prod)
python -m captcha_canary.main
```

## Environment

| var | meaning | default |
|---|---|---|
| `CAPTCHA_GATEWAY_URL` | gateway base URL | `http://localhost:8080` |
| `CANARY_INTERVAL_SEC` | interval between runs | `21600` (6h) |
| `CANARY_FIXTURES_DIR` | fixture directory | `tests/canary/fixtures` |
| `CANARY_PASS_THRESHOLD` | alert when success < this | `0.9` |
| `CANARY_POLL_INTERVAL_SEC` | per-task poll cadence | `5` |
| `CANARY_TASK_TIMEOUT_SEC` | per-task deadline | `180` |
| `CANARY_ALERT_WEBHOOK` | JSON POST target on failure | (unset) |

## Fixture format

```json
{
  "name": "turnstile_demo",
  "clientKey": "canary-key",
  "task": {
    "type": "turnstile",
    "websiteURL": "https://example.com",
    "websiteKey": "0x4AAA..."
  },
  "expect": { "status": "ready", "solution_contains": ["token"] }
}
```

`expect.status` defaults to `ready`. `solution_contains` is the list of
keys the solution must contain. Missing or extra keys are tolerated.
