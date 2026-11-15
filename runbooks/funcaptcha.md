# Runbook: funcaptcha (Arkose Labs / Arkose Bot Manager)

## 1. Snapshot

- **captcha type:** `CaptchaType.FUNCAPTCHA`, `CaptchaType.FUNCAPTCHA_ENTERPRISE`,
  `CaptchaType.FUNCAPTCHA_BDA` — token minted by Arkose, plus the BDA blob alone.
- **antibot family:** Arkose Labs enforcement (not one of our `AntibotEngine` values;
  it is a captcha vendor, so the payload builder lives under `solvers/funcaptcha/`).
- **mode:** `both`. `solve_http` is primary; `solve_browser` drives the real widget
  for games the HTTP path does not model.
- **current success rate target:** not measured. The wire below is ported from public
  RE (2026-07) and exercised only against fixtures, so it keeps the baseline
  `experimental=False` — **flip to `experimental=True` if a canary shows the BDA
  envelope being rejected** (that is the first thing to check).
- **tier:** 3 (skeleton with a complete wire, no live verification).
- **owner:** funcaptcha lane.

## 2. Payload shape

```json
{
  "type": "funcaptcha",
  "websiteURL": "https://target.example/login",
  "websitePublicKey": "3EE79F8D-13A6-474B-9278-448EA19F79B3",
  "surl": "https://client-api.arkoselabs.com",
  "data[blob]": "<optional, from data-adx on the page>",
  "userAgent": "<optional UA override>",
  "cookies": [{"name": "session", "value": "..."}],
  "bda_plaintext": "<optional recorded BDA, used verbatim>",
  "rsa_public_key": "<optional pinned SPKI base64>",
  "bio": "<optional captured interaction trace>",
  "variant": "grid | rotate | match",
  "rows": 2,
  "cols": 3,
  "head": "funcap-v1"
}
```

Required: `websiteURL`, a public key (`websitePublicKey` / `publickey` / `sitekey`).
Enterprise is the same payload; `surl` is the customer verify host
(``https://<customer>-api.arkoselabs.com``). Legacy `/in.php` sends `publickey`,
`pageurl`, `surl`.

## 3. Flow

1. `GET {surl}/v2/{pk}/api.js` — scrape `capi_version`, enforcement build UUID,
   enforcement hash, RSA SPKI (`settings.py`). A build UUID means the BDA field is
   named `c`; without one it stays `bda`.
2. Build the BDA (`bda.py`): recorded plaintext → JS hook → synthesised sensor, in
   that order. Sealed by AES-256-GCM + RSA-OAEP(SHA-256) (`envelope.py`).
3. `POST {surl}/fc/gt2/public_key/{pk}` — ordered form, `ark-build-id` and
   `x-ark-esync-value` headers (`wire.open_session`).
4. A token carrying `sup=1` means a suppressed session, no game. Done.
5. `GET {surl}/fc/gfct/` — `challengeID`, `_challenge_imgs`, instruction.
6. `variants.classify` → rotate / grid / match; the sibling solver answers:
   `rotate` → `solvers/rotate`, `grid`/`match` → the classification head
   (`funcap-v1`, 2x3) via `solvers/classification/funcaptcha_grid.py`.
7. `POST {surl}/fc/ca/` — `session_token`, `game_token`, `guess` (+ `bio` when
   forwarded). `solved` ends the session; a follow-up image set starts another round
   (max 5, `session.py`).
8. Return `SolverResult(token=..., extra={userAgent, path, variant, rounds, bda_source})`.

A game we cannot model, or a variant solver that is unavailable (no `cv2`), leaves as
`NeedsBrowserError`: one lane hop, then the widget answers its own challenge in
`solve_browser` (reads the `fc-token` field, never replays a captured token).

## 4. Sensor / fingerprint

- Schema module: `solvers/funcaptcha/fields.py` (wired tables) + `sensor.py`
  (device/identity sampling) + `devices.py` (pool).
- Hot fields: `f` (K over `fe` values), `ife_hash`, `webgl_hash_webgl`,
  `network_info_rtt_type`, `6a62b2a558` (enforcement hash), `lfasdgs` (build UUID).
- Freshness cadence: per API build. `api.js` rotates the enforcement hash and the
  SPKI, and the build UUID changes the BDA field name.
- Fingerprint store key: `fp:arkose:<ua_hash>` (optional; overlays screen/renderer).
- `K` is MurmurHash3 x64-128 as `hex(h1)||hex(h2)` (`hashes.py`), verified against a
  public reference implementation on 27 vectors.

## 5. Model / dataset

- Model registry key: `funcap-v1` (grid head), `rotate-v1` (angle regressor).
- Weights: `s3://captcha-models/<...>` — see `docs/ml.md`.
- Training set: live-traffic sampling + human-in-loop (`docs/implementation-order.md`).
- Retrain cadence: on drift.
- Expected accuracy: not measured for Arkose's stylized tile set.

## 6. Known failure modes

| error_code | symptom | typical cause | fix |
|---|---|---|---|
| `ERR_SENSOR` | token endpoint rejects the BDA, or api.js is unparsable | sensor schema drift / build rotation | re-check `fields.py` against a fresh bundle, bump `SolverInfo.version` |
| `ERR_SENSOR` | "carries no plaintext RSA key (VM-encoded build)" | SPKI hides inside the stack VM | pin `rsa_public_key` from a capture; static VM extraction is separate RE |
| `ERR_CHALLENGE_EXPIRED` | `fc/ca` says not solved and offers no next round | answer wrong, or session TTL blown | check §7; re-run with a longer budget |
| `ERR_NEEDS_BROWSER` | unknown game type | game we do not model | browser lane; or add a variant branch in `variants.py` |
| `ERR_UNSOLVABLE` | round budget exhausted (5) | answer rejection loop | inspect `extra.rounds`; the game or the head is wrong |

## 7. How to debug

- Fixtures: `tests/solvers/funcaptcha/fixtures/` — loader bundle + the three endpoint
  bodies. Run `pytest tests/solvers/funcaptcha -q`.
- Logs: structlog JSON, filter by `captcha_type=funcaptcha`.
- `SolverResult.extra['bda_source']` says `recorded` / `js` / `sensor` — a
  `sensor` run that gets rejected is a sensor-fidelity problem, not a flow problem.
- `SolverResult.extra['path']` is `suppressed`, `token-only`, `grid`, `rotate`,
  `match` or `browser`.
- `ctx.extra['funcaptcha_bda_builder']` swaps the BDA source for a jssandbox run;
  `ctx.extra['arkose_guess_codec']` swaps the `guess` encoder.

## 8. How to regenerate

- Constants (capi version, build id, enforcement hash) are scraped automatically.
- The RSA key is the only manual step on VM-encoded builds: pin `rsa_public_key`
  from a captured gt2 body (the `c` field decrypts to the BDA under it), or run the
  static extraction described in `docs/reverse-2026.md`.
- Sensor tables (codecs, fonts, devices): capture a fresh Chrome probe output, update
  `fields.py` / `devices.py`, then bump `SolverInfo.version`.

After regen: re-run `pytest tests/solvers/funcaptcha`, update §9.

## 9. Incident log

- 2026-11-15 — wire rewritten — public-RE port replacing the placeholder dispatcher;
  the BDA is now a real sensor snapshot instead of a JS-loader stub. No live
  verification, no incidents yet.

## 10. References

- 2captcha FunCaptcha: <https://2captcha.com/h/how-to-bypass-funcaptcha>
- public RE, deobfuscated bundle + VM disassembly: <https://github.com/buggerlogger/arkose-solver>
- internal: `docs/reverse-2026.md` (no FunCaptcha section yet), `docs/protocol.md`
