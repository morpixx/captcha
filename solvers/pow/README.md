# captcha-solver-pow

Tier 1 pure-CPU proof-of-work solvers. No network, no models, no browser — just
hashing until the bits line up.

## Types

| solver   | `CaptchaType`         | notes                                                                                      |
|----------|------------------------|--------------------------------------------------------------------------------------------|
| friendly | `CaptchaType.FRIENDLY` | per-worker sha256 mining, `D` leading zero bits, parallelized with `ThreadPoolExecutor`    |
| altcha   | `CaptchaType.ALTCHA`   | find `N in [0..max]` where `sha256(salt + str(N))` matches the challenge hex digest        |

## Payload shapes

### `friendly`

Either the compact string form

```json
{"puzzle": "<signature>.<difficulty>.<workers>.<salt_base_hex>"}
```

or the structured form

```json
{"signature": "...", "difficulty": 16, "workers": 4, "salt_base": "<hex>"}
```

Worker input: `salt_base_bytes || worker_index (u32 LE) || nonce (u64 LE)`.
Goal: `leading_zero_bits(sha256(input)) >= difficulty`.

### `altcha`

```json
{"algorithm": "SHA-256", "challenge": "<hex>", "max": 500000, "salt": "...", "signature": "<hex>"}
```

## Result

- `friendly` — `token` is base64 of `{"solutions":[{"worker":i,"nonce":n},...], "response_signature":..., "worker_count":W}`.
- `altcha`   — `token` is `json.dumps({"algorithm","challenge","number","salt","signature","took_ms"})`.

Both populate `SolverResult.extra` with the raw parsed data for debugging.

## Deadlines

Both solvers honor `task.deadline_ts` (wall-clock seconds). They check the
clock every few thousand iterations — cheap, bounded tail latency.

## Entry points

Discovered through `importlib.metadata.entry_points` under the group
`captcha.solvers`. No manual registration.

## Tests

```
uv run --package captcha-solver-pow pytest solvers/pow
```
