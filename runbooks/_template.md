# Runbook: <captcha_type>

> Copy this file to `runbooks/<captcha_type>.md` when adding a new type.
> Every runbook keeps these ten headings in this order. CI (`tools/runbook-check`)
> enforces that code changes under `solvers/<captcha_type>/` land together with
> an edit here.

## 1. Snapshot

- **captcha type:** `<CaptchaType.XXX>` — one-line human description.
- **antibot family:** `<AntibotEngine.XXX>` — vendor + product.
- **mode:** `http` | `browser` | `both` (which `Solver.solve_*` is primary).
- **current success rate target:** `>= NN%` on `tests/fixtures/<type>/golden/`.
- **tier:** 1 (production) | 2 (beta) | 3 (skeleton).
- **owner:** team / person on call for this type.

## 2. Payload shape

The `Task.payload` JSON for this type. Mirrors the 2captcha-compat
`TaskPayload` plus any type-specific keys.

```json
{
  "type": "<CaptchaType value>",
  "websiteURL": "https://target.example/path",
  "websiteKey": "<site-key or null>",
  "pageAction": "<optional action tag>",
  "userAgent": "<optional UA override>",
  "proxy": {"type": "http", "host": "...", "port": 0, "user": "...", "pass": "..."},
  "<type-specific>": "..."
}
```

Required fields: `websiteURL`, `<...>`.
Optional: `proxy`, `userAgent`, `<...>`.

## 3. Flow

What `solve_http` (or `solve_browser`) does, in order:

1. Validate payload, pull `SolverContext`.
2. Acquire proxy / fingerprint / JS runtime.
3. Build sensor payload (see §4).
4. HTTP round-trips to the vendor (list endpoints, methods, bodies).
5. Parse response into `SolverResult(token=..., meta={...})`.
6. Emit metrics (`captcha_solve_duration_seconds`, outcome).

Add a mermaid or ASCII sequence diagram if the flow has > 4 hops.

## 4. Sensor / fingerprint

- Schema module: `packages/sensors/src/captcha_sensors/<engine>.py`.
- Hot fields: `<field1>`, `<field2>`, `<...>`.
- Freshness cadence: `<daily | weekly | per-release>`; regen command below.
- Fingerprint store key: `fp:<engine>:<ua_hash>`.

## 5. Model / dataset

- Model registry key: `captcha_models:<type>:<version>`.
- Weights: `s3://captcha-models/<type>/<version>/model.onnx`.
- Training set: `s3://captcha-datasets/<type>/v<N>/`.
- Retrain cadence: `<every N days | on drift>`.
- Expected accuracy on holdout: `>= NN%`.

If the type has no ML component, write `n/a` under each bullet.

## 6. Known failure modes

| error_code | symptom | typical cause | fix |
|---|---|---|---|
| `UNSOLVABLE` | vendor returns negative verdict | drifted sensor schema | regen sensor, see §8 |
| `CHALLENGE_EXPIRED` | token rejected on submit | > 120s between acquire and use | reduce latency in flow §3 step N |
| `PROXY` | vendor TLS fingerprint block | proxy pool rotating too fast | rotate sticky for N requests |
| `SENSOR` | payload schema mismatch | vendor pushed JS update | regen sensor (§8), bump schema version |

## 7. How to debug

- Fixtures: `tests/fixtures/<type>/` — `.task.json` + `.response.json` pairs.
- Replay one: `python -m captcha_solver_base.harness --type <type> --fixture <name>`.
- Logs: structlog JSON, filter by `captcha_type=<type>`.
- Metrics: Grafana dashboard `captcha / per-type / <type>`.
- Env knobs: `CAPTCHA_LOG_LEVEL=DEBUG`, `CAPTCHA_DUMP_SENSOR=1`.

## 8. How to regenerate

Run when symptom row in §6 points here (schema drift / weights drift):

```
# sensor schema
python tools/sensor-regen --engine <engine> --out packages/sensors/src/captcha_sensors/<engine>.py

# model weights
python tools/train-<type> --dataset s3://captcha-datasets/<type>/v<N>/ --out s3://captcha-models/<type>/<N+1>/
```

After regen: bump `SolverInfo.version`, re-run golden fixtures, update §9.

## 9. Incident log

Reverse chronological. New entries on top. `YYYY-MM-DD — short — trigger / fix`.

- `YYYY-MM-DD — initial setup — runbook created, no live incidents yet.`

## 10. References

- vendor docs: `<url>`
- 2captcha docs for this type: `<url>`
- relevant OSS: `<github link>`
- internal RE notes: `docs/re/<type>.md`
