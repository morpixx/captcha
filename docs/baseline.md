# Baseline (W0)

Снимок на 2026-11-15. Источник — текущий код после `chore/snapshot-20261115` и восстановления backend из `main`.

## Checks

| Команда | Результат |
|---|---|
| `uv run pytest -q` | 262 passed, 3 skipped |
| `uv run ruff check src tests` | clean |
| `uv run lint-imports` | 6/6 contracts kept |
| solver inventory | 56 loaded without cv, 60/62 types |
| missing without cv | `rotate`, `slider` (need `cv2`) |

## Coverage

- 40 of 56 registered solvers are `experimental=True`.
- 53 `# FILL:` markers in `src/captcha`.
- ML inference code exists, no weights in the model bucket, no golden sets.
- hCaptcha type is bound to the grid classifier; `SolutionKind.HCAPTCHA` expects a token.
- No 2captcha official type-name mapping; JSON API accepts snake_case only.
- Proxy composition from `proxyType/proxyAddress/...` absent.
- `Task.payload` stores proxy/cookies in Postgres JSONB; `Account.api_key` plaintext.
- No rate limit/quota.
- CI runs only `tests/domain` and `tests/solvers/pow`.
- mypy is red by docs (~27 errors), not part of CI.

## Browser

- Current tree had no browser lane; restored from `main`: `adapters/browser/*`, `services/worker_browser/*`, `services/canary/*`, `services/pipeline/*`.
- Domain merge added `SolveMode.BROWSER`, `SolverInfo.browser`, `Solver.solve_browser`, `SolverContext.browser`, browser lane, `WORKER_BROWSER_*`, canary/router settings.
- Restored browser pool is Playwright/Camoufox scaffold; hCaptcha needs per-task fresh Camoufox profile with `D:/project` options.
- Measured browser footprint from user: ~700 MB per active page; default sessions set to 12 for 16 GB.

## Hopscotch

- `D:/hopscotch_v2`: DINOv3 ViT-B/16, 9 queries, spatial/appearance/readout.
- 110 manual train / 30 validation / 10 honeypot; 3953 silver train; 400 blind.
- Last run: 282/400 (70.5%) client validation, 27/30 GoldVal; target 95%/80%.
- Training env: Python 3.14, PyTorch 2.13 cu130, RTX 5060 8 GB.
- Service integration: not started; artifact/export/solver pending.

## Known unknowns

- Live success rate for every type: not measured.
- hCaptcha HTTP pass rate for Steam/Epic: not measured (Riot jsdom fails; browser works).
- Which open-source model/architecture is best for all visible captchas: ADR pending.
- Actual RSS per browser on target sites: measure with `CAMOUFOX_MEMORY_LOG`.
- Whether 95% hopscotch is reachable on current labels: unknown.

## Commands

~~~powershell
uv run pytest -q
uv run ruff check src tests
uv run lint-imports
uv run python -c "from captcha.solvers.registry import discover_solvers; print(len(discover_solvers().all_solvers()))"
~~~
