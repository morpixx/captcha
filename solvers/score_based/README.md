# captcha-solver-score-based

Token/score-based captcha solvers where the score is driven by a behavior/sensor
payload and the token itself is produced by a frictionless endpoint. No image
challenge is shown — the antibot grades the request on JA3, fingerprint, IP and
sensor telemetry, then hands back a token.

## Solvers

| Entry point | `CaptchaType` | Status |
| --- | --- | --- |
| `turnstile_invisible` | `TURNSTILE_INVISIBLE` | skeleton — needs CF challenge scrape + shim |
| `recaptcha_v3` | `RECAPTCHA_V3` | skeleton — needs recaptcha v3 sensor builder |
| `recaptcha_v3_enterprise` | `RECAPTCHA_V3_ENTERPRISE` | skeleton — same |

## Why score-based is its own package

Score-based token-only captchas never show pixels and never ask for audio. The
only way to pass is to look right to the grader. That means every solver here
reaches deep into `captcha_sensors` + `captcha_fingerprint` and burns one fresh
session per attempt. A bad score is a soft-fail: upstream returns a low-score
token the caller cannot use. Solvers here raise `UnsolvableError` on verified
low-score responses so the gateway can refund / retry on a different proxy
tier instead of handing the caller a dud.

## Runbook locations

- `runbooks/turnstile.md` — pulling the `cdn-cgi/challenge-platform` JS, shim list.
- `runbooks/recaptcha_v3.md` — current `anchor` / `reload` schema, `bg` sensor shape.

Both runbooks live outside this package (service-ops repo) — commit them
alongside the first live-tested token.
