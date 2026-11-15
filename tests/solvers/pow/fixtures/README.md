# PoW canary fixtures

Inputs are synthetic but wire-exact, not screenshots of a live site: no live
request was made while producing them.

- `friendly_*.json` — friendly-pow v1. `puzzle` is `<signature>.<base64 buffer>`;
  the buffer carries version `1`, a solution count and a difficulty byte. The
  fixture timestamp is frozen, so `expiry` is 0 (never expires) — the expiry path
  is covered by generated puzzles in `test_friendly.py`.
- `altcha_*.json` — altcha-lib v1 payloads. `challenge = hex(hash(salt + number))`;
  the salt ends with `&` exactly like `createChallenge` produces it. Tokens are
  `base64(JSON)`, which is what the widget posts back.

The fixtures are run by `test_canary_fixtures.py` through
`captcha.solvers.harness` (the same loader the canary bot uses) and the solution
is re-verified from the fixture payload, not from the solver's own output.
