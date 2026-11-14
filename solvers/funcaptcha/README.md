# captcha-solver-funcaptcha

Arkose Labs FunCaptcha.

| Entry point | `CaptchaType` | Notes |
| --- | --- | --- |
| `funcaptcha` | `FUNCAPTCHA` | dispatches by game variant |
| `funcaptcha_bda` | `FUNCAPTCHA_BDA` | BDA-only path, used when the gateway needs just the token |
| `funcaptcha_enterprise` | `FUNCAPTCHA_ENTERPRISE` | enterprise endpoint variant |

## What ships here

Two pieces, split on purpose:

1. **`funcaptcha.py`** — the solver that talks to `client-api.arkoselabs.com`,
   picks a game variant, and dispatches. Rotate variants are delegated to the
   `ROTATE` solver via the registry injected in `ctx.extra['solver_registry']`;
   match/3d-match/tile variants raise `CaptchaError` with a FILL marker until
   their per-variant solver lands.

2. **`bda.py`** — `FunCaptchaBDASolver`. Arkose's token endpoint requires a
   `bda` POST field: a base64 blob of browser telemetry produced by the
   obfuscated `v2/<pk>/api.js` loader. We execute that loader in `ctx.js_runtime`
   with a fingerprint-shaped shim. The loader JS is **not** bundled here — the
   worker provides it through `captcha_sensors` or a live scrape, since it
   rotates per public_key.

## Flow summary

    GET  /v2/<pk>/api.js             -> bootstrap, token constant
    POST /fc/gt2/public_key/<pk>     -> {bda, public_key, site, userbrowser, ...}
    JSON response                     -> {token, challenge_url, session_token, ...}
    GET  challenge_url               -> game assets (rotate, match, 3d, ...)
    solve game                        -> answer coords/indices
    POST /fc/ca/                     -> verdict
    collect token

Enterprise flips the subdomain to `<customer>-api.arkoselabs.com` and
sometimes adds a `data[blob]` extra required by the caller's integration;
`task.payload['blob']` carries that through.
